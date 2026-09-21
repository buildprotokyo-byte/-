"""Grounding DINO 本体を合成平面図で実測するベンチマーク。

段階Aの初回検証ではモデル重みを取得できなかったため、アダプタの配線だけを
StaticBackendで確認した。このスクリプトは Hugging Face の公開モデルを実際に
読み込み、clean/light/medium/heavy の4条件で個々の戸・窓を局所化できるかを測る。

評価は以下を分けて報告する。

* 既定しきい値を通過した検出数と ``SymbolCountReading`` の状態
* IoU 0.5 で正解記号に一致した true positive 数、precision、recall
* スコアしきい値を0.10まで下げた診断値

低しきい値の値は採用候補ではなく、「しきい値が厳しすぎるだけか、そもそも
局所化できていないか」を切り分けるための診断である。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from axes.image_axis.grounding_dino_adapter import (
    GroundingDinoAdapter,
    HuggingFaceGroundingDinoBackend,
    ScoredDetection,
)
from benchmarks.synthetic_plans import ALL_LEVELS, Symbol, make_plan


Box = tuple[float, float, float, float]
IOU_THRESHOLD = 0.5
DIAGNOSTIC_SCORE_THRESHOLD = 0.10


def intersection_over_union(left: Box, right: Box) -> float:
    """2つの ``(x1, y1, x2, y2)`` ボックスのIoUを返す。"""
    ix1 = max(left[0], right[0])
    iy1 = max(left[1], right[1])
    ix2 = min(left[2], right[2])
    iy2 = min(left[3], right[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def greedy_true_positives(
    predictions: Iterable[Box], truths: Iterable[Box], *, iou_threshold: float
) -> int:
    """各正解を高々1回だけ使う貪欲照合でtrue positive数を返す。"""
    remaining = list(truths)
    matched = 0
    for prediction in predictions:
        if not remaining:
            break
        best_index, best_truth = max(
            enumerate(remaining),
            key=lambda item: intersection_over_union(prediction, item[1]),
        )
        best_iou = intersection_over_union(prediction, best_truth)
        if best_iou >= iou_threshold:
            matched += 1
            remaining.pop(best_index)
    return matched


@dataclass(frozen=True)
class CategoryMetrics:
    level: str
    category: str
    truth_count: int
    status: str
    count_range: tuple[int, int]
    accepted_count: int
    true_positives: int
    precision: float
    recall: float
    diagnostic_count: int
    diagnostic_true_positives: int
    seconds: float


def _metrics(
    *,
    level: str,
    category: str,
    truth: list[Symbol],
    detections: tuple[ScoredDetection, ...],
    status: str,
    count_range: tuple[int, int],
    seconds: float,
) -> CategoryMetrics:
    truth_boxes = [tuple(float(v) for v in symbol.box) for symbol in truth]
    accepted = [d.box for d in detections if d.verdict != "rejected"]
    true_positives = greedy_true_positives(
        accepted, truth_boxes, iou_threshold=IOU_THRESHOLD
    )
    diagnostic = [
        d.box
        for d in detections
        if min(d.box_score, d.text_score) >= DIAGNOSTIC_SCORE_THRESHOLD
    ]
    diagnostic_true_positives = greedy_true_positives(
        diagnostic, truth_boxes, iou_threshold=IOU_THRESHOLD
    )
    return CategoryMetrics(
        level=level,
        category=category,
        truth_count=len(truth_boxes),
        status=status,
        count_range=count_range,
        accepted_count=len(accepted),
        true_positives=true_positives,
        precision=true_positives / len(accepted) if accepted else 0.0,
        recall=true_positives / len(truth_boxes) if truth_boxes else 0.0,
        diagnostic_count=len(diagnostic),
        diagnostic_true_positives=diagnostic_true_positives,
        seconds=seconds,
    )


def run() -> list[CategoryMetrics]:
    backend = HuggingFaceGroundingDinoBackend()
    adapter = GroundingDinoAdapter(backend)
    results: list[CategoryMetrics] = []

    for level in ALL_LEVELS:
        plan = make_plan(level, seed=7)
        for category in ("door", "window"):
            started = time.perf_counter()
            reading = adapter.detect_category(plan.image, category)
            elapsed = time.perf_counter() - started
            truth = [symbol for symbol in plan.symbols if symbol.kind == category]
            results.append(
                _metrics(
                    level=level,
                    category=category,
                    truth=truth,
                    detections=reading.detections,
                    status=reading.status,
                    count_range=reading.count_range,
                    seconds=elapsed,
                )
            )

    print("level\tcategory\ttruth\tstatus\trange\taccepted\tTP\tprecision\trecall\tdiag>=0.10\tdiagTP\tseconds")
    for item in results:
        print(
            f"{item.level}\t{item.category}\t{item.truth_count}\t{item.status}\t"
            f"{item.count_range}\t{item.accepted_count}\t{item.true_positives}\t"
            f"{item.precision:.3f}\t{item.recall:.3f}\t{item.diagnostic_count}\t"
            f"{item.diagnostic_true_positives}\t{item.seconds:.3f}"
        )
    return results


if __name__ == "__main__":
    run()
