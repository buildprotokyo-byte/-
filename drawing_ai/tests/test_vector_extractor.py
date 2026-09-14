"""Hermetic tests for vector_extractor.py and solid_model_agent.py.

A tiny synthetic PDF is built with PyMuPDF itself (real text + a real
filled path, not an image) so these tests exercise the actual PDF-parsing
code path without depending on any externally supplied drawing file.
"""
from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from drawing_ai import vector_extractor as ve  # noqa: E402
from drawing_ai.agents import solid_model_agent  # noqa: E402
from drawing_ai.config import settings  # noqa: E402
from drawing_ai.ingestion import RenderedSheet  # noqa: E402
from drawing_ai.schemas import ElementReading, Tile  # noqa: E402


def _build_synthetic_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)

    # Scale label, as it appears in a real title block: a "縮尺" label
    # followed by a lone "1/50" token.
    page.insert_text((500, 380), "縮尺", fontsize=8)
    page.insert_text((520, 380), "1/50", fontsize=8)

    # A dimension chain: three components that sum to a separately-placed
    # (and clearly larger) total -- and one unrelated number sharing the
    # same y but placed far away, which must NOT be absorbed into the chain.
    page.insert_text((100, 100), "100", fontsize=8)
    page.insert_text((130, 100), "200", fontsize=8)
    page.insert_text((160, 100), "300", fontsize=8)  # 100+200+300 = 600
    page.insert_text((100, 120), "600", fontsize=8)  # the stated total, below the chain
    page.insert_text((550, 100), "9999", fontsize=8)  # far away, same y -- must not merge in

    # A mismatched chain: components that do NOT sum to their neighbor,
    # to verify a DimensionChainFlag actually gets raised.
    page.insert_text((100, 200), "50", fontsize=8)
    page.insert_text((130, 200), "60", fontsize=8)
    page.insert_text((160, 200), "70", fontsize=8)  # sum = 180
    page.insert_text((100, 220), "184", fontsize=8)  # close-but-wrong total (delta=4, within 3% of 180 but >3mm tolerance)

    # A room ceiling-height annotation, for solid_model_agent to match.
    page.insert_text((250, 150), "CH=2500", fontsize=8)

    # A wall: a filled rectangle in the exact color vector_extractor.py
    # looks for.
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(300, 250, 340, 290))
    shape.finish(fill=(0.494, 0.463, 0.447), color=None)
    shape.commit()

    doc.save(path)
    doc.close()


@pytest.fixture
def synthetic_sheet(tmp_path):
    pdf_path = tmp_path / "synthetic.pdf"
    _build_synthetic_pdf(str(pdf_path))
    return RenderedSheet(
        sheet_id="sheet-test",
        sheet_index=0,
        source_name="synthetic.pdf#page=1",
        image_path=str(tmp_path / "unused.png"),  # vector_extractor never touches this
        width=int(600 * settings.render_dpi / 72),
        height=int(400 * settings.render_dpi / 72),
        source_pdf_path=str(pdf_path),
        source_pdf_page_index=0,
    )


def test_is_vector_native(synthetic_sheet):
    assert ve.is_vector_native(synthetic_sheet)


def test_scale_parsing(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    assert gt is not None
    assert gt.scale_text == "1/50"
    expected = 25.4 * 50 / settings.render_dpi
    assert gt.mm_per_px == pytest.approx(expected)


def test_words_in_bbox_returns_exact_text(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    zoom = settings.render_dpi / 72.0
    # "600" was placed at PDF (100, 120); search a small px window around it.
    x = 100 * zoom
    y = 120 * zoom
    text = ve.words_in_bbox(gt, x - 20, y - 20, x + 60, y + 20)
    assert "600" in text


def test_dimension_chain_confirms_matching_total_without_flag(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    flags = ve.check_dimension_chains(gt)
    # The 100/200/300 -> 600 chain matches exactly (within tolerance) and
    # must NOT produce a flag -- exact matches are silent confirmations.
    assert not any(f.total_raw_text == "600" for f in flags)


def test_dimension_chain_flags_real_mismatch(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    flags = ve.check_dimension_chains(gt)
    assert any(f.total_raw_text == "184" for f in flags)
    flag = next(f for f in flags if f.total_raw_text == "184")
    assert flag.component_sum_mm == pytest.approx(180.0)
    assert flag.delta_mm == pytest.approx(4.0)


def test_far_away_number_does_not_merge_into_chain(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    numeric = [w for w in gt.words if ve._NUMERIC_PATTERN.match(w.text)]
    bands = ve._band_cluster(numeric, key=lambda w: w.cy, perp_key=lambda w: w.cx)
    for band in bands:
        texts = {w.text for w in band}
        if "100" in texts and "200" in texts:
            assert "9999" not in texts


def test_wall_polygon_extraction_and_scale_conversion(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    assert len(gt.wall_polygons_px) == 1
    poly_px = gt.wall_polygons_px[0]
    assert len(poly_px) == 4

    walls_mm = ve.wall_segments_mm(gt)
    assert len(walls_mm) == 1
    xs = [p[0] for p in walls_mm[0].polygon_mm]
    ys = [p[1] for p in walls_mm[0].polygon_mm]
    # The rect was drawn 40x40 PDF-points; real_mm = pdf_pt * 25.4 * scale_denom / 72
    # (see vector_extractor.py's mm_per_px derivation) = 40 * 25.4 * 50 / 72.
    expected_mm = 40 * 25.4 * 50 / 72
    assert max(xs) - min(xs) == pytest.approx(expected_mm, rel=0.01)
    assert max(ys) - min(ys) == pytest.approx(expected_mm, rel=0.01)


def test_solid_model_matches_wall_to_nearest_ceiling_height(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    model = solid_model_agent.build_solid_model({synthetic_sheet.sheet_id: gt}, elements=[], tiles_by_id={})
    assert len(model.wall_segments) == 1
    assert model.wall_segments[0].height_mm == pytest.approx(2500.0)


def test_solid_model_matches_room_element_to_ceiling_height(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    zoom = settings.render_dpi / 72.0
    # Place a fake tile right where "CH=2500" was written (PDF ~250,150).
    tile = Tile(
        tile_id="t1",
        sheet_id=synthetic_sheet.sheet_id,
        sheet_index=0,
        row=0,
        col=0,
        x0=int(240 * zoom),
        y0=int(140 * zoom),
        x1=int(300 * zoom),
        y1=int(170 * zoom),
        image_path="unused.png",
    )
    element = ElementReading(
        element_id="e1",
        element_type="room",
        label_ja="テスト室",
        sheet_id=synthetic_sheet.sheet_id,
        tile_ids=["t1"],
        confidence=0.8,
    )
    model = solid_model_agent.build_solid_model(
        {synthetic_sheet.sheet_id: gt}, elements=[element], tiles_by_id={"t1": tile}
    )
    assert len(model.room_heights) == 1
    assert model.room_heights[0].ceiling_height_mm == pytest.approx(2500.0)
    assert model.room_heights[0].room_label == "テスト室"


def test_extract_table_rows_groups_by_row(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    zoom = settings.render_dpi / 72.0
    # Covers the y=100 row (100/200/300/9999, spread across a wide x range --
    # extract_table_rows groups purely by row, it deliberately does not
    # split a row on a large x gap the way dimension-chain clustering does,
    # since a real table row can legitimately have far-apart cells) and the
    # y=120 row (the single "600" total) from _build_synthetic_pdf above.
    rows = ve.extract_table_rows(gt, x0=0, y0=90 * zoom, x1=600 * zoom, y1=130 * zoom)
    assert ["100", "200", "300", "9999"] in rows
    assert ["600"] in rows


def test_extract_table_rows_empty_bbox_returns_empty(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    assert ve.extract_table_rows(gt, x0=0, y0=0, x1=1, y1=1) == []


def test_estimate_gross_footprint_uses_largest_chain_per_axis(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    result = ve.estimate_gross_footprint(gt)
    assert result is not None
    width_mm, depth_mm, basis = result
    # Horizontal axis (same-y band): 100+200+300=600 beats the lone 9999
    # only if 9999 doesn't join that band (it's far away, per
    # test_far_away_number_does_not_merge_into_chain) -- so the winning
    # horizontal band is either the 100/200/300 chain (600) or the
    # 50/60/70 chain (180); the former is larger.
    assert width_mm == pytest.approx(600.0)
    assert "外形概算" in basis


def test_estimate_gross_footprint_returns_none_without_enough_numeric_words(tmp_path):
    import fitz

    pdf_path = tmp_path / "sparse.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((50, 50), "100", fontsize=8)
    doc.save(str(pdf_path))
    doc.close()

    sheet = RenderedSheet(
        sheet_id="sparse",
        sheet_index=0,
        source_name="sparse.pdf#page=1",
        image_path=str(tmp_path / "unused.png"),
        width=int(200 * settings.render_dpi / 72),
        height=int(200 * settings.render_dpi / 72),
        source_pdf_path=str(pdf_path),
        source_pdf_page_index=0,
    )
    gt = ve.extract_ground_truth(sheet)
    assert ve.estimate_gross_footprint(gt) is None


def test_no_vector_ground_truth_for_plain_image(tmp_path):
    sheet = RenderedSheet(
        sheet_id="sheet-img",
        sheet_index=0,
        source_name="scan.png",
        image_path=str(tmp_path / "scan.png"),
        width=100,
        height=100,
        source_pdf_path=None,
        source_pdf_page_index=None,
    )
    assert ve.is_vector_native(sheet) is False
    assert ve.extract_ground_truth(sheet) is None
