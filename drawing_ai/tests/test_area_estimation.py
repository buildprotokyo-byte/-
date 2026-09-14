from __future__ import annotations

import pytest

from drawing_ai import area_parsing
from drawing_ai.agents import detail_agent, parent_agent
from drawing_ai.schemas import SiteFact, Tile


def _tile(ocr_text: str = "") -> Tile:
    return Tile(
        tile_id="t1",
        sheet_id="s1",
        sheet_index=0,
        row=0,
        col=0,
        x0=0,
        y0=0,
        x1=100,
        y1=100,
        image_path="unused.png",
        ocr_text=ocr_text,
    )


def test_parse_area_text_prefers_printed_sqm_over_tatami():
    result = area_parsing.parse_area_text("5.3m2|3.2畳")
    assert result == (5.3, "printed_sqm")


def test_parse_area_text_falls_back_to_tatami_conversion():
    result = area_parsing.parse_area_text("12.5畳")
    assert result == pytest.approx((12.5 * 1.62, "tatami_conversion"))


def test_parse_area_text_handles_full_width_symbol():
    assert area_parsing.parse_area_text("45.2㎡") == (45.2, "printed_sqm")


def test_parse_area_text_returns_none_for_no_match():
    assert area_parsing.parse_area_text("特記事項のみ") is None
    assert area_parsing.parse_area_text("") is None


def test_reconcile_elements_populates_area_sqm_from_area_text():
    tile = _tile()
    passes = [
        [
            {
                "element_type": "room",
                "label_ja": "リビング",
                "attributes": {},
                "area_text": "12.5m2",
                "confidence": 0.8,
            }
        ]
    ]
    out = detail_agent._reconcile_elements(passes, tile)
    assert len(out) == 1
    assert out[0].area_sqm == 12.5
    assert out[0].area_source == "printed_sqm"


def test_reconcile_elements_leaves_area_unset_when_no_area_text():
    tile = _tile()
    passes = [[{"element_type": "room", "label_ja": "洋室", "attributes": {}, "confidence": 0.7}]]
    out = detail_agent._reconcile_elements(passes, tile)
    assert out[0].area_sqm is None
    assert out[0].area_source == "none"


def test_reconcile_elements_estimates_area_from_grounded_width_depth():
    # Ungrounded tile (no vector ground truth) -- width/depth accepted at
    # face value, same as any other ungrounded VLM reading elsewhere.
    tile = _tile()
    passes = [
        [
            {
                "element_type": "room",
                "label_ja": "洋室-B",
                "attributes": {},
                "width_text": "3600",
                "depth_text": "2700",
                "confidence": 0.6,
            }
        ]
    ]
    out = detail_agent._reconcile_elements(passes, tile)
    assert out[0].area_sqm == pytest.approx(9.72)
    assert out[0].area_source == "width_depth_estimate"


def test_reconcile_elements_rejects_ungrounded_width_depth_pair_when_vector_gt_available():
    tile = Tile(
        tile_id="t1", sheet_id="s1", sheet_index=0, row=0, col=0,
        x0=0, y0=0, x1=100, y1=100, image_path="unused.png",
        ground_truth_text="洋室-B 3600 は書かれているが奥行の数字は無い",
        has_vector_ground_truth=True,
    )
    passes = [
        [
            {
                "element_type": "room",
                "label_ja": "洋室-B",
                "attributes": {},
                "width_text": "3600",
                "depth_text": "9999",  # not present in ground truth -- unverifiable
                "confidence": 0.6,
            }
        ]
    ]
    out = detail_agent._reconcile_elements(passes, tile)
    assert out[0].area_sqm is None
    assert out[0].area_source == "none"


def test_reconcile_elements_accepts_grounded_width_depth_pair_when_vector_gt_available():
    tile = Tile(
        tile_id="t1", sheet_id="s1", sheet_index=0, row=0, col=0,
        x0=0, y0=0, x1=100, y1=100, image_path="unused.png",
        ground_truth_text="洋室-B 3600 2700 という実測値",
        has_vector_ground_truth=True,
    )
    passes = [
        [
            {
                "element_type": "room",
                "label_ja": "洋室-B",
                "attributes": {},
                "width_text": "3600",
                "depth_text": "2700",
                "confidence": 0.6,
            }
        ]
    ]
    out = detail_agent._reconcile_elements(passes, tile)
    assert out[0].area_sqm == pytest.approx(9.72)
    assert out[0].area_source == "width_depth_estimate"


def test_area_text_takes_priority_over_width_depth_estimate():
    tile = _tile()
    passes = [
        [
            {
                "element_type": "room",
                "label_ja": "洋室-B",
                "attributes": {},
                "area_text": "12.0m2",
                "width_text": "3600",
                "depth_text": "2700",
                "confidence": 0.6,
            }
        ]
    ]
    out = detail_agent._reconcile_elements(passes, tile)
    assert out[0].area_sqm == 12.0
    assert out[0].area_source == "printed_sqm"


def test_aggregate_phase1_flags_numeric_disagreement_across_sources():
    facts = [
        SiteFact(
            key="gross_footprint_sqm", label_ja="延床面積(概算)", value="65.0", unit="m2",
            confidence=0.55, source_sheet_ids=["sheet1"], verified_by_vector=True,
        ),
        SiteFact(
            key="gross_footprint_sqm", label_ja="延床面積(概算)", value="90.0", unit="m2",
            confidence=0.7, source_sheet_ids=["sheet2"], verified_by_vector=True,
        ),
    ]
    result = parent_agent.aggregate_phase1(facts)
    assert len(result.facts) == 1
    assert result.facts[0].value == "90.0"  # higher-confidence one kept
    assert any("食い違い" in q for q in result.unresolved_questions)


def test_aggregate_phase1_does_not_flag_agreeing_sources():
    facts = [
        SiteFact(
            key="gross_footprint_sqm", label_ja="延床面積(概算)", value="65.0", unit="m2",
            confidence=0.55, source_sheet_ids=["sheet1"], verified_by_vector=True,
        ),
        SiteFact(
            key="gross_footprint_sqm", label_ja="延床面積(概算)", value="66.0", unit="m2",
            confidence=0.7, source_sheet_ids=["sheet2"], verified_by_vector=True,
        ),
    ]
    result = parent_agent.aggregate_phase1(facts)
    assert not any("食い違い" in q for q in result.unresolved_questions)
