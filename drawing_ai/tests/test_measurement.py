"""Tests for measurement.py -- see its module docstring for the two
drawing-reading techniques this implements (dimension-chain projection,
三角スケール pixel-to-real conversion) and README 3.11 for the real-data
finding that scoped ``project_span_mm`` down to label-proximity matching
only, after a fuller grid-reconstruction attempt failed against real data.
"""
from __future__ import annotations

import pytest

from drawing_ai import measurement as m
from drawing_ai.vector_extractor import GroundTruthWord


def _word(text: str, cx: float, cy: float) -> GroundTruthWord:
    # a small synthetic bbox centered on (cx, cy) -- enough for the cx/cy
    # properties measurement.py actually reads.
    return GroundTruthWord(text=text, x0=cx - 5, y0=cy - 5, x1=cx + 5, y1=cy + 5)


def test_pixel_distance_to_mm_uses_scale():
    # 3-4-5 triangle: 30px horizontal, 40px vertical -> 50px hypotenuse
    mm = m.pixel_distance_to_mm((0, 0), (30, 40), mm_per_px=2.0)
    assert mm == 100.0  # 50px * 2mm/px


def test_polygon_area_sqm_unit_square():
    # a 1000px x 1000px square at mm_per_px=1 -> 1,000,000 mm^2 = 1 m^2
    square = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    assert m.polygon_area_sqm(square, mm_per_px=1.0) == 1.0


def test_polygon_area_sqm_applies_scale_squared():
    square = [(0, 0), (100, 0), (100, 100), (0, 100)]
    # mm_per_px=10 -> each side is 1000mm -> area 1,000,000 mm^2 = 1 m^2
    assert m.polygon_area_sqm(square, mm_per_px=10.0) == 1.0


def test_project_span_mm_matches_label_in_range():
    chain = [_word("1700", 100, 500), _word("800", 200, 500), _word("4350", 400, 500)]
    result = m.project_span_mm(chain, lo_px=190, hi_px=210)
    assert result is not None
    assert result.total_mm == 800.0
    assert result.matched_texts == ["800"]


def test_project_span_mm_returns_none_when_nothing_in_range():
    chain = [_word("1700", 100, 500), _word("800", 200, 500), _word("4350", 400, 500)]
    result = m.project_span_mm(chain, lo_px=1000, hi_px=1050)
    assert result is None


def test_project_span_mm_rejects_chain_far_away_on_perpendicular_axis():
    # regression test for a real false positive found against page-4 data
    # (README 3.11): a chain whose x-range coincidentally overlaps the
    # target must still be rejected if it sits far away in y.
    far_away_chain = [_word("900", 200, 50), _word("800", 300, 50)]  # cy=50
    result = m.project_span_mm(far_away_chain, lo_px=190, hi_px=210, perp_px=2000, max_perp_distance_px=400)
    assert result is None  # chain's cy=50 is 1950px from perp_px=2000 -- too far


def test_project_span_mm_accepts_chain_near_on_perpendicular_axis():
    nearby_chain = [_word("900", 200, 1950), _word("800", 300, 1950)]  # cy=1950
    result = m.project_span_mm(nearby_chain, lo_px=190, hi_px=210, perp_px=2000, max_perp_distance_px=400)
    assert result is not None
    assert result.total_mm == 900.0


def test_project_span_mm_sums_multiple_matched_labels():
    chain = [_word("100", 100, 500), _word("200", 110, 500), _word("300", 400, 500)]
    # both 100 and 200's labels fall inside a wide target range
    result = m.project_span_mm(chain, lo_px=90, hi_px=120)
    assert result is not None
    assert result.total_mm == 300.0
    assert set(result.matched_texts) == {"100", "200"}


def test_calibrate_axis_from_anchors_recovers_known_scale():
    # a perfect axis: px = 50 + mm * 0.2 (i.e. 5mm/px)
    anchors = [(50.0, 0.0), (250.0, 1000.0), (450.0, 2000.0)]
    cal = m.calibrate_axis_from_anchors(anchors)
    assert cal is not None
    assert cal.px_per_mm == pytest.approx(0.2)
    assert cal.origin_px == pytest.approx(50.0)
    assert cal.residual_stdev_px == pytest.approx(0.0, abs=1e-9)
    assert cal.mm_of(250.0) == pytest.approx(1000.0)


def test_calibrate_axis_from_anchors_reports_noise_when_anchors_disagree():
    # not perfectly co-linear -- the third anchor is off the line the first
    # two define; residual_stdev_px must reflect that rather than hiding it
    anchors = [(50.0, 0.0), (250.0, 1000.0), (500.0, 2000.0)]  # third should be ~450
    cal = m.calibrate_axis_from_anchors(anchors)
    assert cal is not None
    assert cal.residual_stdev_px > 10.0


def test_calibrate_axis_from_anchors_needs_at_least_two():
    assert m.calibrate_axis_from_anchors([(50.0, 0.0)]) is None
    assert m.calibrate_axis_from_anchors([]) is None
