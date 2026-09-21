"""Grounding DINOのタイル分割(SAHI方式)が検出精度を改善するかを実測する。

背景
----
段階Aの実測(`docs/stage_a_report.md`、PR #1 commit `c8e4e81`)で、Grounding DINOの
最高スコア検出は図面全体を囲む矩形になり、個々の記号の位置とは無関係で、
スコアが高いほど不正解という逆相関が見つかった。この現象は「モデルが一度に見る
画像が大きすぎ、小さな記号よりも画像全体の方が"それらしい"と判定してしまう」
ことが原因と考えられる。

このスクリプトは `axes/image_axis/sahi_tiling.py` の `SlicedInferenceBackend` を使い、
画像を256x256のタイルに分割してから検出させた場合に、全体画像を1回で検出させる
既存方式(`benchmarks/run_grounding_dino_eval.py`)と比べて、IoU 0.5での
precision/recall/F1がどう変わるかを比較する。

正直な制約
----------
- 古典的テンプレート照合との比較はしていない(PR #1 commit `c8e4e81`が既に実施済み)。
  ここではタイル分割の有無という1変数だけを比較する
- タイルサイズ・重なり率は1組(256px, 25%)のみを試した。パラメータ探索はしていない
- 1図面・1乱数シード(seed=7)のみでの結果

実行: ``python -m benchmarks.run_sahi_tiling_eval``
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from axes.image_axis.grounding_dino_adapter import (
    GroundingDinoAdapter,
    HuggingFaceGroundingDinoBackend,
    ScoredDetection,
)
from axes.image_axis.sahi_tiling import SlicedInferenceBackend, TilingConfig
from benchmarks.run_grounding_dino_eval import greedy_true_positives
from benchmarks.synthetic_plans import ALL_LEVELS, Symbol, make_plan

IOU_THRESHOLD = 0.5


@dataclass(frozen=True)
class ComparisonRow:
    level: str
    category: str
    method: str
    truth_count: int
    status: str
    count_range: tuple[int, int]
    accepted_count: int
    true_positives: int
    precision: float
    recall: float
    f1: float
    seconds: float


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _row(
    *,
    level: str,
    category: str,
    method: str,
    truth: list[Symbol],
    detections: tuple[ScoredDetection, ...],
    status: str,
    count_range: tuple[int, int],
    seconds: float,
) -> ComparisonRow:
    truth_boxes = [tuple(float(v) for v in symbol.box) for symbol in truth]
    accepted = [d.box for d in detections if d.verdict != "rejected"]
    true_positives = greedy_true_positives(accepted, truth_boxes, iou_threshold=IOU_THRESHOLD)
    precision = true_positives / len(accepted) if accepted else 0.0
    recall = true_positives / len(truth_boxes) if truth_boxes else 0.0
    return ComparisonRow(
        level=level,
        category=category,
        method=method,
        truth_count=len(truth_boxes),
        status=status,
        count_range=count_range,
        accepted_count=len(accepted),
        true_positives=true_positives,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        seconds=seconds,
    )


def run(tiling_config: TilingConfig = TilingConfig()) -> list[ComparisonRow]:
    base_backend = HuggingFaceGroundingDinoBackend()
    whole_adapter = GroundingDinoAdapter(base_backend)
    tiled_adapter = GroundingDinoAdapter(SlicedInferenceBackend(base_backend, tiling_config))

    rows: list[ComparisonRow] = []
    for level in ALL_LEVELS:
        plan = make_plan(level, seed=7)
        for category in ("door", "window"):
            truth = [symbol for symbol in plan.symbols if symbol.kind == category]

            started = time.perf_counter()
            whole_reading = whole_adapter.detect_category(plan.image, category)
            whole_seconds = time.perf_counter() - started
            rows.append(
                _row(
                    level=level, category=category, method="whole_image",
                    truth=truth, detections=whole_reading.detections,
                    status=whole_reading.status, count_range=whole_reading.count_range,
                    seconds=whole_seconds,
                )
            )

            started = time.perf_counter()
            tiled_reading = tiled_adapter.detect_category(plan.image, category)
            tiled_seconds = time.perf_counter() - started
            rows.append(
                _row(
                    level=level, category=category, method="sahi_tiled",
                    truth=truth, detections=tiled_reading.detections,
                    status=tiled_reading.status, count_range=tiled_reading.count_range,
                    seconds=tiled_seconds,
                )
            )

    header = (
        f"{'level':<7}{'category':<9}{'method':<12}{'truth':>6}{'status':<15}"
        f"{'range':>10}{'accepted':>9}{'TP':>4}{'precision':>10}{'recall':>8}"
        f"{'F1':>7}{'seconds':>9}"
    )
    print(header)
    for row in rows:
        print(
            f"{row.level:<7}{row.category:<9}{row.method:<12}{row.truth_count:>6}"
            f"{row.status:<15}{str(row.count_range):>10}{row.accepted_count:>9}"
            f"{row.true_positives:>4}{row.precision:>10.3f}{row.recall:>8.3f}"
            f"{row.f1:>7.3f}{row.seconds:>9.2f}"
        )

    print("\n=== whole_image vs sahi_tiled: F1の平均 ===")
    for method in ("whole_image", "sahi_tiled"):
        values = [row.f1 for row in rows if row.method == method]
        print(f"{method}: 平均F1 = {sum(values) / len(values):.3f} (n={len(values)})")

    return rows


if __name__ == "__main__":
    run()
