"""Grounding DINOのタイル分割(SAHI方式)が検出精度を改善するかを実測する。

背景
----
段階Aの実測(`docs/stage_a_report.md` 9章、PR #1 commit `c8e4e81`)で、
Grounding DINOは全16条件で古典的テンプレート照合に負け、さらに**確信度スコアが
正しさと逆相関している**ことが分かった。正解記号と重なる検出の最高スコアより、
重ならない検出(多くは図面全体を囲む矩形)の最高スコアの方が高いという事象が、
全条件で例外なく起きている(9-4節)。これはしきい値の調整では直せない。

この逆相関の仮説的な原因は「モデルに一度に見せる画像が大きすぎ、小さな記号よりも
画像全体の方が"それらしい"と判定してしまう」ことにある。SAHI(Slicing Aided
Hyper Inference)方式でタイルに分割すれば、モデルに「画像全体を囲む」という
選択肢自体を与えないため、この逆相関が解消するのではないか、というのが検証したい
仮説である。

このスクリプトは2つを測る。

1. **精度比較**: 全体画像を1回で検出させる既存方式と、256px・重なり25%の
   タイルに分割してから検出させる方式(`axes/image_axis/sahi_tiling.py`)で、
   IoU 0.5/0.10でのprecision/recall/F1・個数誤差がどう変わるか
2. **スコア逆相関の再検証**: `docs/stage_a_report.md` 9-4節と同じ方法
   (正解と重なる検出の最高スコア vs 重ならない検出の最高スコア)を、
   タイル分割後の検出結果に対しても行い、逆相関がタイル分割で解消するかを見る

正直な制約
----------
- タイルサイズ・重なり率は1組(256px, 25%)のみを試した。パラメータ探索はしていない
- 1図面・1乱数シード(seed=0、9章と条件を揃えるため)のみでの結果
- プロンプトはカテゴリごとの既定1種類のみ(9章のようなプロンプト4種の振り比較はしていない)
- 古典的テンプレート照合との比較はしていない(9章が既に実施済みで、ここではタイル分割の
  有無という1変数だけを見る)

実行: ``python -m benchmarks.run_sahi_tiling_eval``
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from axes.image_axis.grounding_dino_adapter import (
    CategoryConfig,
    GroundingDinoAdapter,
    HuggingFaceGroundingDinoBackend,
    RawDetection,
)
from axes.image_axis.sahi_tiling import SlicedInferenceBackend, TilingConfig
from benchmarks.run_grounding_dino_eval import IOU_LOOSE, IOU_STRICT, MatchResult, iou, match_greedy, truth_boxes
from benchmarks.synthetic_plans import ALL_LEVELS, DegradationLevel, make_plan

#: 9章のスコア分布分析(gdino_score_stats)と条件を揃えるための、しきい値を掛ける前の
#: 生スコアの下限。この値自体は「見えているスコアの範囲」を広げるだけで、
#: 採否判定には使わない。
RAW_SCORE_FLOOR = 0.02

PROMPTS = {
    "door": "a door symbol in a floor plan",
    "window": "a window symbol in a floor plan",
}


@dataclass
class AccuracyRow:
    level: str
    category: str
    method: str
    iou_name: str
    n_truth: int
    n_pred: int
    tp: int
    precision: float
    recall: float
    f1: float
    count_error: int


@dataclass
class ScoreCorrelationRow:
    level: str
    category: str
    method: str
    n_raw: int
    n_overlapping_truth: int
    max_score_overlapping: float | None
    max_score_not_overlapping: float | None
    inverted: bool | None  # True: 逆相関(重ならない方が高スコア), False: 正しい向き, None: 比較不可


def _collect_raw(
    backend, level: DegradationLevel, category: str, seed: int
) -> list[RawDetection]:
    plan = make_plan(level, seed=seed)
    return list(backend.detect(plan.image, PROMPTS[category]))


def _accuracy_rows(
    level: DegradationLevel, category: str, method: str, detections: list[RawDetection], seed: int
) -> list[AccuracyRow]:
    plan = make_plan(level, seed=seed)
    truths = truth_boxes(plan, category)
    rows = []
    for iou_name, min_iou in (("strict_0.50", IOU_STRICT), ("loose_0.10", IOU_LOOSE)):
        # 段階Aのアダプタ既定しきい値(box>=0.30 and text>=0.30)を通過した検出だけを候補にする。
        accepted = [d.box for d in detections if d.box_score >= 0.30 and d.text_score >= 0.30]
        result: MatchResult = match_greedy(accepted, truths, min_iou)
        rows.append(
            AccuracyRow(
                level=str(level), category=category, method=method, iou_name=iou_name,
                n_truth=result.n_truth, n_pred=result.n_pred, tp=result.true_positive,
                precision=round(result.precision, 3), recall=round(result.recall, 3),
                f1=round(result.f1, 3), count_error=result.count_error,
            )
        )
    return rows


def _score_correlation_row(
    level: DegradationLevel, category: str, method: str, detections: list[RawDetection], seed: int
) -> ScoreCorrelationRow:
    plan = make_plan(level, seed=seed)
    truths = truth_boxes(plan, category)
    hit_scores: list[float] = []
    miss_scores: list[float] = []
    for d in detections:
        best_iou = max((iou(d.box, t) for t in truths), default=0.0)
        (hit_scores if best_iou >= IOU_LOOSE else miss_scores).append(d.box_score)
    max_hit = max(hit_scores) if hit_scores else None
    max_miss = max(miss_scores) if miss_scores else None
    inverted = None if max_hit is None or max_miss is None else max_miss > max_hit
    return ScoreCorrelationRow(
        level=str(level), category=category, method=method,
        n_raw=len(detections), n_overlapping_truth=len(hit_scores),
        max_score_overlapping=round(max_hit, 3) if max_hit is not None else None,
        max_score_not_overlapping=round(max_miss, 3) if max_miss is not None else None,
        inverted=inverted,
    )


def run(tiling_config: TilingConfig = TilingConfig(), seed: int = 0) -> dict[str, object]:
    whole_backend = HuggingFaceGroundingDinoBackend(raw_score_floor=RAW_SCORE_FLOOR)
    tiled_backend = SlicedInferenceBackend(
        HuggingFaceGroundingDinoBackend(raw_score_floor=RAW_SCORE_FLOOR), tiling_config
    )

    accuracy_rows: list[AccuracyRow] = []
    correlation_rows: list[ScoreCorrelationRow] = []

    for level in ALL_LEVELS:
        for category in ("door", "window"):
            for method, backend in (("whole_image", whole_backend), ("sahi_tiled", tiled_backend)):
                started = time.perf_counter()
                detections = _collect_raw(backend, level, category, seed)
                elapsed = time.perf_counter() - started
                accuracy_rows.extend(_accuracy_rows(level, category, method, detections, seed))
                correlation_rows.append(_score_correlation_row(level, category, method, detections, seed))
                print(
                    f"[{method:11s}] {level:7s} {category:7s} raw={len(detections):3d} "
                    f"{elapsed:6.1f}s",
                    flush=True,
                )

    print("\n=== 精度比較(IoU 0.50 / 0.10) ===")
    header = (
        f"{'level':<7}{'category':<9}{'method':<12}{'iou':<12}{'truth':>6}"
        f"{'pred':>6}{'tp':>4}{'precision':>10}{'recall':>8}{'F1':>7}{'count_err':>10}"
    )
    print(header)
    for row in accuracy_rows:
        print(
            f"{row.level:<7}{row.category:<9}{row.method:<12}{row.iou_name:<12}"
            f"{row.n_truth:>6}{row.n_pred:>6}{row.tp:>4}{row.precision:>10.3f}"
            f"{row.recall:>8.3f}{row.f1:>7.3f}{row.count_error:>10}"
        )

    print("\n=== 平均F1(whole_image vs sahi_tiled) ===")
    for method in ("whole_image", "sahi_tiled"):
        for iou_name in ("strict_0.50", "loose_0.10"):
            values = [r.f1 for r in accuracy_rows if r.method == method and r.iou_name == iou_name]
            print(f"{method:12s} {iou_name:12s} 平均F1={sum(values)/len(values):.3f} (n={len(values)})")

    print("\n=== スコア逆相関の再検証(9-4節と同じ方法) ===")
    header2 = (
        f"{'level':<7}{'category':<9}{'method':<12}{'n_raw':>6}{'n_hit':>6}"
        f"{'max_hit':>9}{'max_miss':>9}{'逆相関':>8}"
    )
    print(header2)
    for row in correlation_rows:
        inverted_str = "?" if row.inverted is None else ("YES" if row.inverted else "no")
        print(
            f"{row.level:<7}{row.category:<9}{row.method:<12}{row.n_raw:>6}"
            f"{row.n_overlapping_truth:>6}"
            f"{str(row.max_score_overlapping):>9}{str(row.max_score_not_overlapping):>9}"
            f"{inverted_str:>8}"
        )

    for method in ("whole_image", "sahi_tiled"):
        comparable = [r for r in correlation_rows if r.method == method and r.inverted is not None]
        inverted_count = sum(1 for r in comparable if r.inverted)
        print(
            f"\n{method}: 逆相関が起きた条件 {inverted_count}/{len(comparable)}"
            f"(比較不可 {sum(1 for r in correlation_rows if r.method == method) - len(comparable)}件)"
        )

    payload = {
        "seed": seed,
        "tiling_config": asdict(tiling_config),
        "accuracy": [vars(r) for r in accuracy_rows],
        "score_correlation": [vars(r) for r in correlation_rows],
    }
    with open("benchmarks/sahi_tiling_results.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("\n書き出しました: benchmarks/sahi_tiling_results.json")

    return payload


if __name__ == "__main__":
    run()
