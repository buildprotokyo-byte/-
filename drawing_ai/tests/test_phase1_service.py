"""Tests for phase1_service.run_phase1 -- the VLM-free Phase 1 pipeline
(real ingestion/tiling/merge/classify code, no orchestrator.run_pipeline
dependency). OCR itself is stubbed here (mirrors this repo's existing
practice of stubbing the VLM -- see tests/conftest.py) so these tests don't
depend on Tesseract/PaddleOCR being installed; the real-engine behavior is
already validated against 千倉相川邸 (README 3.13/3.14).
"""
from __future__ import annotations

from PIL import Image

from drawing_ai import phase1_service
from drawing_ai.config import settings
from drawing_ai.ocr_engine import OcrResult, OcrWord


def _make_page_image(path, width=200, height=150):
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(path)


def test_run_phase1_single_page_all_green(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tile_px", 200)
    monkeypatch.setattr(settings, "tile_overlap_px", 20)

    img_path = tmp_path / "page1.png"
    _make_page_image(img_path)

    def _fake_ocr_tile_any(image_path: str) -> OcrResult:
        return OcrResult(words=[OcrWord(text="2730", confidence=0.95, box=(10, 10, 40, 25))])
    monkeypatch.setattr(phase1_service, "ocr_tile", _fake_ocr_tile_any)

    result = phase1_service.run_phase1([str(img_path)], tmp_path / "work", stop_after_flagged=10)

    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.green_count == 1
    assert page.amber_count == 0
    assert page.red_count == 0
    assert result.stopped_early is False
    assert page.words[0]["text"] == "2730"
    assert page.words[0]["tier"] == "green"


def test_run_phase1_stops_after_flagged_threshold(tmp_path, monkeypatch):
    # tile_px larger than the test image -> exactly one tile per page, so
    # the word count below isn't affected by merge_tile_words' dedup logic
    # (that logic has its own dedicated tests in test_ocr_merge.py).
    monkeypatch.setattr(settings, "tile_px", 300)
    monkeypatch.setattr(settings, "tile_overlap_px", 20)

    # two single-page "files" (simplest way to get 2 sheets without a
    # multi-page PDF fixture) -- each page's one tile returns 6 unrecognized
    # words (all red under classify_word's no-confidence-info default),
    # so page 1 alone already exceeds a stop_after_flagged=5 threshold.
    img1 = tmp_path / "page1.png"
    img2 = tmp_path / "page2.png"
    _make_page_image(img1)
    _make_page_image(img2)

    def _fake_ocr_tile_many_red(image_path: str) -> OcrResult:
        # low confidence (<0.5) -> classify_word tiers these "red" (see
        # README 3.13: OCR confidence, not the pattern whitelist, decides
        # free-text tiering when a real engine's confidence is available)
        return OcrResult(words=[
            OcrWord(text=f"謎語{i}", confidence=0.2, box=(i * 10, 10, i * 10 + 8, 25))
            for i in range(6)
        ])
    monkeypatch.setattr(phase1_service, "ocr_tile", _fake_ocr_tile_many_red)

    result = phase1_service.run_phase1([str(img1), str(img2)], tmp_path / "work", stop_after_flagged=5)

    assert result.stopped_early is True
    assert result.stopped_after_page == 1
    assert len(result.pages) == 1, "must not process page 2 once the threshold is exceeded on page 1"
    assert result.total_flagged == 6


def test_run_phase1_respects_max_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tile_px", 200)
    monkeypatch.setattr(settings, "tile_overlap_px", 20)
    img1 = tmp_path / "page1.png"
    img2 = tmp_path / "page2.png"
    _make_page_image(img1)
    _make_page_image(img2)

    monkeypatch.setattr(phase1_service, "ocr_tile", lambda p: OcrResult(words=[]))

    result = phase1_service.run_phase1([str(img1), str(img2)], tmp_path / "work", stop_after_flagged=None, max_pages=1)
    assert len(result.pages) == 1
