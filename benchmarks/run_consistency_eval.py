"""ステップ2・4: 整合性軸(arbitration/consistency_solver.py)を、
段階Aの合成図面データ(benchmarks/synthetic_plans.py)で検証する。

記号認識について(段階Aと同じ、正直な制約)
------------------------------------------------------------
Grounding DINO は huggingface.co への到達が遮断されているため、段階Aと同様に
実際の推論は行っていません。代わりに、``benchmarks/run_ifc_eval.py`` が使った
のと同じ「正解の記号位置にどれだけインクが残っているか」というプロキシ判定
(``READABLE_RATIO`` / ``NOISE_RATIO``)を再利用し、その判定結果を
``axes/image_axis/grounding_dino_adapter.py`` の ``StaticBackend`` に注入して
本物の ``GroundingDinoAdapter`` を実際に走らせています。つまり測っているのは
「Grounding DINO の検出精度」ではなく、**同じ入力を整合性軸に通したときに、
段階Aで既に確認済みの読み取り劣化(記号の見落とし)を正しく矛盾として拾える
かどうか**です。

通常ケースと矛盾ケース
----------------------
- 通常ケース: 劣化なし(clean)。4部屋・戸4・窓4がすべて読み取れる
- 矛盾ケース: 中度劣化(medium)+ 古典的二値化(median=1)。段階Aの報告書
  (docs/stage_a_report.md 表2「中度 古典(median=1)」)で**実測済みの**、
  窓を1つ読み落とす条件をそのまま再利用する。ここで新しく数値を作ってはいない

強い軸 / 弱い軸
----------------
- room_count(IfcOpenShell が構築した空間階層の部屋数): 強い軸。合成図面の
  部屋一覧から直接構築しており、この検証内では実質的に既知の値
- door_count / window_count(Grounding DINO アダプタの読み取り): 強い軸。
  ただし ``status == "abstained"``(1件も検出できない)の場合は
  consistency_solver 側の設計により自動的に制約から除外される
- 関係式(door_count >= room_count, window_count >= room_count): 強い軸。
  IfcOpenShell の ``space_without_door`` / ``space_without_window`` と
  同じ絶対ルールを、宣言的な制約として再現したもの
- symbol_total_count 推定(VTracer の連結成分数): **弱い軸**。段階Aで
  「VTracerは記号の読み取りを改善しない、劣化と共に偽の図形が急増する」
  ことが実測済みのため、ハードな制約には使わず参考情報としてのみ扱う
  (詳細は docs/stage_b_report.md 4節)

実行: ``python -m benchmarks.run_consistency_eval``
"""

from __future__ import annotations

import time
import statistics
from dataclasses import dataclass

import cv2
import numpy as np

from arbitration.consistency_solver import ConsistencySolver, SolveResult
from axes.image_axis.grounding_dino_adapter import (
    CategoryConfig,
    GroundingDinoAdapter,
    RawDetection,
    StaticBackend,
    SymbolCountReading,
)
from axes.image_axis.vtracer_vectorizer import binarize_classical, measure_linework, vectorize
from benchmarks.synthetic_plans import DegradationLevel, SyntheticPlan, make_plan, symbol_ink_masks, wall_ink_mask

#: run_ifc_eval.py と同じ判定基準(重複コードだが、段階Aのファイルには手を
#: 加えない方針のためここに複製する)。
SYMBOL_DILATION = 9
WALL_DILATION = 3
READABLE_RATIO = 0.5
NOISE_RATIO = 2.0


def _ellipse(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))


@dataclass(frozen=True)
class SymbolProbe:
    region: np.ndarray
    expected_ink: int


def _symbol_probes() -> dict[int, SymbolProbe]:
    """run_ifc_eval._symbol_probes() と同一のロジック(段階Aのファイルは変更しない)。"""
    walls = cv2.dilate(wall_ink_mask(), _ellipse(WALL_DILATION)) > 0
    probes: dict[int, SymbolProbe] = {}
    for index, ink in symbol_ink_masks().items():
        region = (cv2.dilate(ink, _ellipse(SYMBOL_DILATION)) > 0) & ~walls
        probes[index] = SymbolProbe(region=region, expected_ink=int(((ink > 0) & region).sum()))
    return probes


def _grounding_dino_reading(
    category: str,
    plan: SyntheticPlan,
    probes: dict[int, SymbolProbe],
    mask: np.ndarray,
) -> SymbolCountReading:
    """指定カテゴリの記号について、インク残存率を StaticBackend に注入し、
    実際の GroundingDinoAdapter を通して SymbolCountReading を得る。

    しきい値を明確に超えるスコア(0.9 / 0.9)を与えているため、「読めた」と
    判定された記号はすべて confident 扱いになる(閾値ロジックそのものは
    段階Aのテストで既に固定済みなので、ここでは検証しない)。
    """
    backend = StaticBackend()
    detections: list[RawDetection] = []
    for index, symbol in enumerate(plan.symbols):
        if symbol.kind != category:
            continue
        probe = probes[index]
        ink = int((mask & probe.region).sum())
        ratio = (ink / probe.expected_ink) if probe.expected_ink else 0.0
        if READABLE_RATIO <= ratio <= NOISE_RATIO:
            detections.append(RawDetection(box=symbol.box, box_score=0.9, text_score=0.9, label=category))

    prompt = f"a {category} symbol in a floor plan"
    backend.set_response(prompt, detections)
    adapter = GroundingDinoAdapter(backend, categories=[CategoryConfig(category, prompt)])
    dummy_image = np.zeros((1, 1), dtype=np.uint8)  # StaticBackend は画像の中身を見ない
    return adapter.detect_category(dummy_image, category)


def _vtracer_symbol_total_estimate(plan: SyntheticPlan) -> SymbolCountReading:
    """VTracer の連結成分数から、記号総数(戸+窓)の粗い見積もりを作る(弱い軸)。

    段階A(benchmarks/run_vtracer_eval.py)で実測済みのとおり、劣化が進むと
    偽の図形が急増するため、この見積もりは劣化レベルによって大きくぶれる。
    そのことを整合性軸の中で明示するのが、この関数を弱い軸として登録する目的。
    """
    from benchmarks.run_vtracer_eval import symbol_region  # 段階Aの公開関数を再利用

    otsu_ink = cv2.bitwise_not(binarize_classical(plan.image, 1))
    drawing = vectorize(otsu_ink, filter_speckle=4)
    mask = drawing.to_mask()
    region = symbol_region(plan)
    masked = cv2.bitwise_and(mask, mask, mask=(region > 0).astype(np.uint8) * 255)
    measurement = measure_linework(masked)

    n = measurement.component_count
    # 「連結成分1個 ≒ 記号1個」という粗い仮定に、±2個の余裕を持たせる。
    # 段階Aの実測(偽の図形の急増)を踏まえ、これ自体が信頼できる推定でないことを
    # 前提にした、あくまで参考情報用のレンジ。
    lower = max(0, n - 2)
    upper = n + 2
    return SymbolCountReading(
        category="symbol_total_estimate",
        prompt="(VTracer 連結成分数からの粗い見積もり。検出プロンプトは無い)",
        count_range=(lower, upper),
        status="low_confidence",
        evidence={"raw_component_count": n},
    )


@dataclass
class CaseResult:
    name: str
    level: DegradationLevel
    front_end: str
    doors_read: SymbolCountReading
    windows_read: SymbolCountReading
    result: SolveResult
    build_seconds: float


def run_case(
    name: str,
    level: DegradationLevel,
    *,
    classical_ksize: int,
    seed: int = 7,
) -> CaseResult:
    plan = make_plan(level, seed=seed)
    probes = _symbol_probes()
    mask = binarize_classical(plan.image, classical_ksize) > 0

    start = time.perf_counter()

    door_reading = _grounding_dino_reading("door", plan, probes, mask)
    window_reading = _grounding_dino_reading("window", plan, probes, mask)
    vtracer_reading = _vtracer_symbol_total_estimate(plan)

    solver = ConsistencySolver()
    solver.add_variable(
        "room_count",
        len(plan.rooms),
        len(plan.rooms),
        axis="absolute_rule_axis",
        evidence={"source": "IfcOpenShell 空間階層に登録された部屋数"},
    )
    solver.add_variable_from_reading("door_count", door_reading, axis="image_axis")
    solver.add_variable_from_reading("window_count", window_reading, axis="image_axis")

    # door_count / window_count が(検出0件で)棄権した場合は、対応する変数が
    # 存在せず add_relation が KeyError になるため、明示的にガードする。
    # これ自体が段階Bの制約: 強い軸が完全に棄権すると、その要素についての
    # 矛盾検出そのものが成立しなくなる(docs/stage_b_report.md 7節で報告)。
    if solver.has_variable("door_count"):
        solver.add_relation(
            "door_ge_room",
            "door_count",
            ">=",
            "room_count",
            description="IfcOpenShell space_without_door 相当: 各部屋に出入口が最低1つ必要",
        )
    if solver.has_variable("window_count"):
        solver.add_relation(
            "window_ge_room",
            "window_count",
            ">=",
            "room_count",
            description="IfcOpenShell space_without_window 相当: 各居室に採光開口が最低1つ必要",
        )

    if solver.has_variable("door_count") and solver.has_variable("window_count"):
        solver.add_variable("symbol_total_count", 0, 10_000, axis="derived")
        solver.add_relation(
            "symbol_total_is_door_plus_window",
            "symbol_total_count",
            "==",
            lambda v: v["door_count"] + v["window_count"],
            description="会計上の恒等式(戸+窓=記号総数)。VTracer の弱い軸と突き合わせるための土台",
        )
        solver.add_advisory_reading("symbol_total_count", vtracer_reading, axis="vtracer")

    result = solver.solve()
    build_seconds = time.perf_counter() - start

    return CaseResult(
        name=name,
        level=level,
        front_end=f"古典(median={classical_ksize})",
        doors_read=door_reading,
        windows_read=window_reading,
        result=result,
        build_seconds=build_seconds,
    )


def naive_ratio_check(door_count: int, window_count: int, room_count: int) -> list[str]:
    """比較対象: 制約充足エンジンを使わない、単純な比率チェック。

    「戸・窓の合計が部屋数の2倍未満なら異常」という、閾値1本だけの素朴な
    チェック。効果測定(ステップ4)の比較対象として使う。
    """
    findings = []
    if door_count + window_count < room_count * 2:
        findings.append(
            f"戸+窓の合計({door_count + window_count})が部屋数の2倍未満"
            f"({room_count * 2})"
        )
    return findings


def run_distribution_mismatch_case() -> CaseResult:
    """比較用の合成ケース(合成図面からの読み取りではなく、手で組んだもの)。

    戸2・窓6・部屋4で、**合計(8)は部屋数の2倍(8)と一致する**ため単純な比率
    チェックは異常なしと判定するが、戸の内訳を見ると door_count(2) < room_count(4)
    で「戸のない部屋がある」という矛盾を含む。単純な比率チェックが見逃す偽陰性を、
    整合性軸が拾えるかを見るための、意図的に作ったケース。
    """
    door_reading = SymbolCountReading("door", "(手動構成)", (2, 2), "confident")
    window_reading = SymbolCountReading("window", "(手動構成)", (6, 6), "confident")

    solver = ConsistencySolver()
    solver.add_variable("room_count", 4, 4, axis="absolute_rule_axis")
    solver.add_variable_from_reading("door_count", door_reading, axis="image_axis")
    solver.add_variable_from_reading("window_count", window_reading, axis="image_axis")
    solver.add_relation("door_ge_room", "door_count", ">=", "room_count")
    solver.add_relation("window_ge_room", "window_count", ">=", "room_count")

    result = solver.solve()
    return CaseResult(
        name="比較ケース(合計は一致・内訳が破綻/手動構成)",
        level="clean",
        front_end="(画像処理なし)",
        doors_read=door_reading,
        windows_read=window_reading,
        result=result,
        build_seconds=0.0,
    )


def main() -> list[CaseResult]:
    cases = [
        run_case("通常ケース(劣化なし)", "clean", classical_ksize=1),
        run_case(
            "矛盾ケース(中度劣化・窓1つ見落とし)", "medium", classical_ksize=1
        ),
        run_distribution_mismatch_case(),
    ]

    print("=== ステップ2: 段階Aの合成図面データでの検証 ===\n")
    for case in cases:
        print(f"--- {case.name} ({case.level} / {case.front_end}) ---")
        print(f"door reading:   status={case.doors_read.status} range={case.doors_read.count_range}")
        print(f"window reading: status={case.windows_read.status} range={case.windows_read.count_range}")
        print(case.result.describe())
        print(f"solve() 所要時間: {case.result.solve_seconds * 1000:.3f} ms")
        print(f"読み取り〜求解の一気通し所要時間: {case.build_seconds * 1000:.3f} ms")
        print()

    print("=== ステップ4: 単純な比率チェックとの比較 ===\n")
    for case in cases:
        door_count = case.doors_read.count_range[1]
        window_count = case.windows_read.count_range[1]
        naive = naive_ratio_check(door_count, window_count, 4)
        print(f"--- {case.name} ---")
        print(f"整合性軸: {'矛盾なし(sat)' if case.result.is_consistent else '矛盾あり(unsat): ' + ', '.join(case.result.conflicting_constraints)}")
        print(f"単純な比率チェック: {'異常なし' if not naive else '; '.join(naive)}")
        print()

    timings = benchmark_solver()
    print("=== ステップ4: solve() 200回の処理時間 ===\n")
    print(f"中央値: {timings['median_ms']:.3f} ms")
    print(f"平均: {timings['mean_ms']:.3f} ms")
    print(f"95パーセンタイル: {timings['p95_ms']:.3f} ms")
    print(f"最大: {timings['max_ms']:.3f} ms\n")

    return cases


def benchmark_solver(iterations: int = 200) -> dict[str, float]:
    """通常ケース相当のソルバーを組み直し、solve()全体の時間を反復測定する。"""
    if iterations < 1:
        raise ValueError("iterations は1以上で指定してください")
    elapsed_ms: list[float] = []
    for _ in range(iterations):
        solver = ConsistencySolver()
        solver.add_variable("room_count", 4, 4, axis="absolute_rule_axis")
        solver.add_variable("door_count", 4, 4, axis="image_axis")
        solver.add_variable("window_count", 4, 4, axis="image_axis")
        solver.add_variable("symbol_total_count", 0, 10_000, axis="derived")
        solver.add_relation("door_ge_room", "door_count", ">=", "room_count")
        solver.add_relation("window_ge_room", "window_count", ">=", "room_count")
        solver.add_relation(
            "symbol_total_is_door_plus_window",
            "symbol_total_count",
            "==",
            lambda variables: variables["door_count"] + variables["window_count"],
        )
        result = solver.solve()
        if not result.is_consistent:
            raise AssertionError("通常ケースがunsatになりました")
        elapsed_ms.append(result.solve_seconds * 1000)

    ordered = sorted(elapsed_ms)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "median_ms": statistics.median(elapsed_ms),
        "mean_ms": statistics.mean(elapsed_ms),
        "p95_ms": ordered[p95_index],
        "max_ms": max(elapsed_ms),
    }


if __name__ == "__main__":
    main()
