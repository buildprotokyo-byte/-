"""Grounding DINO実測ベンチマークの評価関数だけを固定するテスト。"""

from benchmarks.run_grounding_dino_eval import (
    greedy_true_positives,
    intersection_over_union,
)


def test_iou_identical_boxes_is_one() -> None:
    box = (1.0, 2.0, 5.0, 8.0)
    assert intersection_over_union(box, box) == 1.0


def test_iou_disjoint_boxes_is_zero() -> None:
    assert intersection_over_union((0, 0, 2, 2), (3, 3, 5, 5)) == 0.0


def test_greedy_matching_does_not_reuse_truth() -> None:
    truth = [(0, 0, 10, 10)]
    predictions = [(0, 0, 10, 10), (0, 0, 10, 10)]
    assert greedy_true_positives(predictions, truth, iou_threshold=0.5) == 1
