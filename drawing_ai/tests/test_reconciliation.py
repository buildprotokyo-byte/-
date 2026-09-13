from __future__ import annotations

from drawing_ai.agents import detail_agent, parent_agent
from drawing_ai.schemas import DimensionReading, SiteFact, SymbolReading, Tile


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


def test_reconcile_dimensions_boosts_agreement_and_grounds_on_ocr():
    tile = _tile(ocr_text="壁芯距離 2,730 その他のテキスト")
    passes = [
        [{"raw_text": "2,730", "value": 2730, "unit": "mm", "role_ja": "壁芯-芯距離", "confidence": 0.6}],
        [{"raw_text": "2,730", "value": 2730, "unit": "mm", "role_ja": "壁芯-芯距離", "confidence": 0.65}],
    ]
    out = detail_agent._reconcile_dimensions(passes, tile)
    assert len(out) == 1
    # base 0.65 + agreement bonus 0.15 + ocr grounding bonus 0.1, clamped to 1.0
    assert out[0].confidence > 0.65
    assert out[0].raw_text == "2,730"


def test_reconcile_dimensions_penalizes_ungrounded_reading():
    tile = _tile(ocr_text="関係のない別の文字列だけ")
    passes = [[{"raw_text": "9,999", "value": 9999, "confidence": 0.7}]]
    out = detail_agent._reconcile_dimensions(passes, tile)
    assert len(out) == 1
    assert out[0].confidence < 0.7  # OCR grounding penalty applied


def test_reconcile_dimensions_keeps_disagreeing_readings_separate():
    tile = _tile()
    passes = [
        [{"raw_text": "1,000", "confidence": 0.5}],
        [{"raw_text": "2,000", "confidence": 0.5}],
    ]
    out = detail_agent._reconcile_dimensions(passes, tile)
    assert {d.raw_text for d in out} == {"1,000", "2,000"}


def test_parent_dedupe_dimensions_prefers_higher_confidence():
    tile_sheet_map = {"tileA": "sheet1", "tileB": "sheet1"}
    a = DimensionReading(tile_id="tileA", raw_text="2,730", confidence=0.6)
    b = DimensionReading(tile_id="tileB", raw_text="2,730", confidence=0.9)
    out = parent_agent._dedupe_dimensions([a, b], tile_sheet_map)
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_parent_dedupe_dimensions_keeps_same_text_on_different_sheets():
    tile_sheet_map = {"tileA": "sheet1", "tileB": "sheet2"}
    a = DimensionReading(tile_id="tileA", raw_text="2,730", confidence=0.6)
    b = DimensionReading(tile_id="tileB", raw_text="2,730", confidence=0.9)
    out = parent_agent._dedupe_dimensions([a, b], tile_sheet_map)
    assert len(out) == 2


def test_parent_dedupe_symbols():
    tile_sheet_map = {"tileA": "sheet1", "tileB": "sheet1"}
    a = SymbolReading(tile_id="tileA", symbol_text_or_glyph="WC", meaning_ja="コンセント", confidence=0.5)
    b = SymbolReading(tile_id="tileB", symbol_text_or_glyph="WC", meaning_ja="コンセント", confidence=0.8)
    out = parent_agent._dedupe_symbols([a, b], tile_sheet_map)
    assert len(out) == 1
    assert out[0].confidence == 0.8


def test_aggregate_phase1_completeness_and_review_flags():
    facts = [
        SiteFact(key="site_area_sqm", label_ja="敷地面積", value="165.29", confidence=0.95),
        SiteFact(key="address", label_ja="所在地", value="東京都..", confidence=0.4, needs_human_review=True),
    ]
    result = parent_agent.aggregate_phase1(facts)
    assert len(result.facts) == 2
    # 2 of 7 expected keys found -> completeness = 2/7
    assert abs(result.completeness_score - (2 / 7)) < 1e-6
    # missing keys + the low-confidence fact should both surface as unresolved questions
    assert any("前面道路幅員" in q for q in result.unresolved_questions)
    assert any("所在地" in q for q in result.unresolved_questions)


def test_aggregate_phase1_merges_duplicate_key_with_confidence_boost():
    facts = [
        SiteFact(
            key="site_area_sqm",
            label_ja="敷地面積",
            value="165.29",
            confidence=0.5,
            source_tile_ids=["t1"],
        ),
        SiteFact(
            key="site_area_sqm",
            label_ja="敷地面積",
            value="165.29",
            confidence=0.5,
            source_tile_ids=["t2"],
        ),
    ]
    result = parent_agent.aggregate_phase1(facts)
    assert len(result.facts) == 1
    assert result.facts[0].confidence > 0.5
    assert set(result.facts[0].source_tile_ids) == {"t1", "t2"}
