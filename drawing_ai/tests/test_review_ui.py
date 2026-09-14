"""Tests for the review_ui package (see README 3.10): the export functions
that turn real vector_extractor/solid_model_agent output into review-page
data, and the template renderers that turn that data into an actual page.

Reuses the same synthetic-PDF pattern as test_vector_extractor.py so these
stay hermetic (no dependency on an externally supplied drawing file).
"""
from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from drawing_ai import vector_extractor as ve  # noqa: E402
from drawing_ai.agents import solid_model_agent  # noqa: E402
from drawing_ai.config import settings  # noqa: E402
from drawing_ai.ingestion import RenderedSheet  # noqa: E402
from drawing_ai.review_ui import character_review, dimension_review, render, solid_preview  # noqa: E402


def _build_synthetic_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((450, 380), "縮尺", fontsize=8, fontname="japan")
    page.insert_text((520, 380), "1/50", fontsize=8)
    page.insert_text((100, 100), "100", fontsize=8)
    page.insert_text((130, 100), "200", fontsize=8)
    page.insert_text((160, 100), "300", fontsize=8)
    page.insert_text((100, 120), "600", fontsize=8)
    page.insert_text((100, 200), "50", fontsize=8)
    page.insert_text((130, 200), "60", fontsize=8)
    page.insert_text((160, 200), "70", fontsize=8)
    page.insert_text((100, 220), "184", fontsize=8)  # flagged mismatch: 50+60+70=180 != 184
    page.insert_text((250, 150), "CH=2500", fontsize=8)
    page.insert_text((260, 160), "下駄箱", fontsize=8, fontname="japan")  # known-vocab word -> should classify green
    page.insert_text((270, 170), "ワ子ゴミ", fontsize=8, fontname="japan")  # garbled-looking -> should classify red
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
        sheet_id="sheet-test", sheet_index=0, source_name="synthetic.pdf#page=1",
        image_path=str(tmp_path / "unused.png"),
        width=int(600 * settings.render_dpi / 72), height=int(400 * settings.render_dpi / 72),
        source_pdf_path=str(pdf_path), source_pdf_page_index=0,
    )


def test_classify_word_known_vocab_is_green():
    tier, _ = character_review.classify_word("下駄箱")
    assert tier == "green"


def test_classify_word_plain_dimension_is_green():
    tier, _ = character_review.classify_word("2730")
    assert tier == "green"


def test_classify_word_unrecognized_garble_is_red():
    tier, _ = character_review.classify_word("ワ子ゴミ")
    assert tier == "red"


def test_build_character_review_data_splits_text_and_numeric_channels(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    data = character_review.build_character_review_data(gt, page_key="p0")
    rows = data["p0"]
    assert any(r["text"] == "下駄箱" and r["channel"] == "text" and r["tier"] == "green" for r in rows)
    assert any(r["text"] == "600" and r["channel"] == "numeric" and r["tier"] == "green" for r in rows)
    assert any(r["text"] == "ワ子ゴミ" and r["tier"] == "red" for r in rows)


def test_build_dimension_review_data_flags_real_mismatch(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    data = dimension_review.build_dimension_review_data(gt, sheet_label="test-sheet")
    assert data["scale_text"] == "1/50"
    flags = data["dimension_chain_flags"]
    assert any(f["total_raw_text"] == "184" and f["component_sum_mm"] == pytest.approx(180.0) for f in flags)
    # the confirmed (non-flagged) chain must NOT appear as a flag
    assert not any(f["total_raw_text"] == "600" for f in flags)


def test_build_solid_preview_data_includes_matched_wall_height(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    model = solid_model_agent.build_solid_model({"sheet-test": gt}, elements=[], tiles_by_id={})
    data = solid_preview.build_solid_preview_data(gt, model, sheet_id="sheet-test")
    assert data["walls"], "the synthetic wall-fill rectangle should produce at least one wall"
    assert any(w["height_mm"] == 2500.0 for w in data["walls"])


def test_render_character_review_embeds_real_data(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    words = character_review.build_character_review_data(gt, page_key="p0")
    html = render.render_character_review(
        words, project_label="テスト物件", image_map={"p0": {"src": "p0.png", "w": 600, "h": 400, "label": "p0"}},
        active_page="p0", db_doc_path="review/test-sheet",
    )
    assert "__WORDS_JSON__" not in html
    assert "__CARDS_JSON__" not in html
    assert "テスト物件" in html
    assert "下駄箱" in html


def test_render_solid_preview_embeds_real_data(synthetic_sheet):
    gt = ve.extract_ground_truth(synthetic_sheet)
    model = solid_model_agent.build_solid_model({"sheet-test": gt}, elements=[], tiles_by_id={})
    data = solid_preview.build_solid_preview_data(gt, model, sheet_id="sheet-test")
    html = render.render_solid_preview(data, img_w=600, img_h=400, project_label="テスト物件")
    assert "__DATA_JSON__" not in html
    assert "テスト物件" in html
