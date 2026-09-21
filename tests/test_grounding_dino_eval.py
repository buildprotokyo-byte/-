"""`benchmarks/run_grounding_dino_eval.py` の照合ロジックのテスト。

実測値そのものは環境とモデルに依存するので固定しません。固定するのは
**「その実測値を出した照合の規則」**の方です。ここがずれると報告書の数字の意味が
変わってしまうため。
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from benchmarks.run_grounding_dino_eval import (
    IOU_LOOSE,
    IOU_STRICT,
    MEDIAN_KSIZES,
    _nms,
    _symbol_templates,
    iou,
    match_greedy,
    template_detect,
    truth_boxes,
)
from benchmarks.synthetic_plans import make_plan


# ---------------------------------------------------------------------------
# IoU
# ---------------------------------------------------------------------------


def test_iou_identical_boxes_is_one() -> None:
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)


def test_iou_disjoint_boxes_is_zero() -> None:
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_touching_edges_is_zero() -> None:
    """辺が接しているだけでは重なっていない。"""
    assert iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0


def test_iou_half_overlap() -> None:
    # 重なり 50、和 150。
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_iou_is_symmetric() -> None:
    a, b = (3.0, 4.0, 11.0, 9.0), (7.0, 1.0, 13.0, 8.0)
    assert iou(a, b) == pytest.approx(iou(b, a))


# ---------------------------------------------------------------------------
# 照合
# ---------------------------------------------------------------------------


def test_match_greedy_perfect() -> None:
    truths = [(0, 0, 10, 10), (50, 50, 60, 60)]
    result = match_greedy(list(truths), truths, IOU_STRICT)
    assert (result.true_positive, result.false_positive, result.false_negative) == (2, 0, 0)
    assert result.f1 == pytest.approx(1.0)
    assert result.count_error == 0


def test_match_greedy_counts_extra_predictions_as_false_positive() -> None:
    truths = [(0, 0, 10, 10)]
    preds = [(0, 0, 10, 10), (100, 100, 110, 110)]
    result = match_greedy(preds, truths, IOU_STRICT)
    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.count_error == 1
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(1.0)


def test_match_greedy_is_one_to_one() -> None:
    """1 つの正解に 2 つの予測が重なっても、真陽性は 1 件だけ。

    これが崩れると「大きな箱を 1 つ出せば全部当たる」ことになってしまう。
    """
    truths = [(0, 0, 10, 10)]
    preds = [(0, 0, 10, 10), (1, 1, 11, 11)]
    result = match_greedy(preds, truths, IOU_LOOSE)
    assert result.true_positive == 1
    assert result.false_positive == 1


def test_match_greedy_rejects_below_min_iou() -> None:
    truths = [(0, 0, 10, 10)]
    preds = [(9, 9, 19, 19)]  # わずかに重なるだけ
    assert match_greedy(preds, truths, IOU_STRICT).true_positive == 0


def test_count_error_is_signed() -> None:
    """個数誤差は符号付き。多く数えたのか少なく数えたのかを潰さない。"""
    truths = [(0, 0, 10, 10), (50, 50, 60, 60)]
    assert match_greedy([], truths, IOU_LOOSE).count_error == -2


def test_empty_prediction_gives_zero_precision_not_error() -> None:
    result = match_greedy([], [(0, 0, 10, 10)], IOU_LOOSE)
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


# ---------------------------------------------------------------------------
# NMS
# ---------------------------------------------------------------------------


def test_nms_keeps_highest_score_among_overlapping() -> None:
    boxes = [(0, 0, 10, 10), (1, 1, 11, 11), (100, 100, 110, 110)]
    kept = _nms(boxes, [0.5, 0.9, 0.7])
    assert (1, 1, 11, 11) in kept
    assert (100, 100, 110, 110) in kept
    assert len(kept) == 2


def test_nms_keeps_all_when_disjoint() -> None:
    boxes = [(0, 0, 10, 10), (50, 50, 60, 60), (200, 200, 210, 210)]
    assert len(_nms(boxes, [0.1, 0.2, 0.3])) == 3


# ---------------------------------------------------------------------------
# テンプレート(古典側のベースライン)
# ---------------------------------------------------------------------------


def test_templates_exist_for_both_kinds() -> None:
    for kind in ("door", "window"):
        templates = _symbol_templates(kind)
        assert templates, f"{kind} のテンプレートが作れていない"
        for _name, patch, _offset in templates:
            assert patch.size > 0
            assert (patch < 128).any(), "テンプレートにインクが入っていない"


def test_median_ksizes_include_one() -> None:
    """1 を外すと窓記号の 1px 線がメディアンで消え、古典側が不当に弱くなる。

    実測で確認した落とし穴なので、回帰しないようにここで固定する。
    """
    assert 1 in MEDIAN_KSIZES


def test_median_blur_destroys_thin_window_lines() -> None:
    """上のテストの根拠: 3x3 メディアンは窓の細線を実際に消す。"""
    plan = make_plan("clean")
    templates = _symbol_templates("window")

    def best_response(image: np.ndarray) -> float:
        return max(
            float(cv2.matchTemplate(image, t, cv2.TM_CCOEFF_NORMED).max())
            for _n, t, _o in templates
        )

    raw = best_response(plan.image)
    blurred = best_response(cv2.medianBlur(plan.image, 3))
    assert raw > 0.9, "劣化なしの図面で窓が照合できないのはおかしい"
    assert blurred < raw / 2, "メディアンで細線が落ちるという前提が崩れている"


def test_template_matching_finds_all_symbols_on_clean_plan() -> None:
    """劣化なしなら、古典的テンプレート照合は戸も窓も全て見つけられる。

    Grounding DINO との比較の土台になる事実なので固定しておく。
    """
    plan = make_plan("clean")
    for kind, threshold in (("door", 0.74), ("window", 0.66)):
        preds = template_detect(plan.image, kind, threshold, median_ksize=1)
        result = match_greedy(preds, truth_boxes(plan, kind), IOU_STRICT)
        assert result.f1 == pytest.approx(1.0), f"{kind}: {result}"


def test_truth_boxes_match_plan_counts() -> None:
    plan = make_plan("clean")
    assert len(truth_boxes(plan, "door")) == plan.door_count == 4
    assert len(truth_boxes(plan, "window")) == plan.window_count == 4
