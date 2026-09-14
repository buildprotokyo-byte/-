"""Phase 1 (文字・数字・記号) as a standalone, VLM-free service.

Extracted from the ad-hoc scratchpad script used to validate this against
the real 千倉相川邸 PDF (README 3.13/3.14): real ingestion.load_drawing_set
-> tile_sheet -> ocr_engine.ocr_tile -> ocr_engine.merge_tile_words ->
review_ui.character_review.classify_word, with zero VLM dependency, so this
runs anywhere Tesseract (or PaddleOCR, where reachable) is installed --
notably it does NOT need a local VLM server, unlike orchestrator.run_pipeline.

This is deliberately scoped to Phase 1 only (get character/number/symbol
recognition right first, per explicit instruction) -- Phase 2/3 (intent,
detail dimensions, quantity take-off) build on top of this once a human has
confirmed Phase 1's output is trustworthy for a given drawing set.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import ingestion
from .ocr_engine import ocr_tile, merge_tile_words
from .review_ui import character_review
from .vector_extractor import GroundTruthWord


@dataclass
class Phase1PageResult:
    page_key: str
    page_no: int
    image_path: str
    width: int
    height: int
    words: list[dict]
    green_count: int
    amber_count: int
    red_count: int


@dataclass
class Phase1Result:
    pages: list[Phase1PageResult] = field(default_factory=list)
    stopped_early: bool = False
    stopped_after_page: int | None = None
    total_flagged: int = 0
    ocr_engine: str = ""


def run_phase1(
    file_paths: list[str],
    work_dir: str | Path,
    *,
    stop_after_flagged: int | None = 10,
    max_pages: int | None = None,
) -> Phase1Result:
    """Run the real OCR-only Phase 1 pipeline against one or more drawing
    files (PDF or image).

    ``stop_after_flagged``: stop processing further pages once the running
    total of non-green (amber+red) words exceeds this count, so a caller
    (e.g. the review UI) never has to present a human reviewer with an
    unbounded backlog in one batch. ``None`` disables the cap (process every
    page / up to ``max_pages``). Each *page* is still processed as a whole
    (merge_tile_words needs every tile of a sheet together to dedupe the
    overlap band -- see README 3.14), so the cap is checked between pages,
    not between tiles.
    """
    from .ocr_engine import get_ocr_engine

    work_dir = Path(work_dir)
    sheets_dir = work_dir / "sheets"
    tiles_dir = work_dir / "tiles"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    sheets = ingestion.load_drawing_set(file_paths, sheets_dir)
    engine_name = type(get_ocr_engine()).__name__

    result = Phase1Result(ocr_engine=engine_name)
    flagged_total = 0

    for sheet in sheets:
        if max_pages is not None and len(result.pages) >= max_pages:
            break

        page_no = sheet.sheet_index + 1
        page_key = str(sheet.sheet_index)
        tiles = ingestion.tile_sheet(sheet, tiles_dir)

        tiles_with_words = []
        for t in tiles:
            ocr_result = ocr_tile(t.image_path)
            words = [
                GroundTruthWord(
                    text=w.text,
                    x0=t.x0 + w.box[0], y0=t.y0 + w.box[1],
                    x1=t.x0 + w.box[2], y1=t.y0 + w.box[3],
                    confidence=w.confidence,
                )
                for w in ocr_result.words
            ]
            tiles_with_words.append((t, words))

        merged = merge_tile_words(tiles_with_words)

        tier_counts = {"green": 0, "amber": 0, "red": 0}
        rows = []
        for i, w in enumerate(merged):
            tier, reason = character_review.classify_word(w.text, ocr_confidence=w.confidence)
            tier_counts[tier] += 1
            rows.append({
                "id": f"{page_key}-w{i}",
                "text": w.text,
                "x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1,
                "tier": tier,
                "reason": reason,
                "confidence": w.confidence,
                "channel": "numeric" if any(c.isdigit() for c in w.text) else "text",
            })

        flagged_total += tier_counts["amber"] + tier_counts["red"]

        page_image_dest = work_dir / f"page-{page_key}.png"
        shutil.copy(sheet.image_path, page_image_dest)

        result.pages.append(Phase1PageResult(
            page_key=page_key, page_no=page_no,
            image_path=str(page_image_dest),
            width=sheet.width, height=sheet.height,
            words=rows,
            green_count=tier_counts["green"],
            amber_count=tier_counts["amber"],
            red_count=tier_counts["red"],
        ))
        result.total_flagged = flagged_total

        if stop_after_flagged is not None and flagged_total > stop_after_flagged:
            result.stopped_early = True
            result.stopped_after_page = page_no
            break

    return result
