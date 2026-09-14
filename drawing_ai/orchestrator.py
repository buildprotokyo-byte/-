"""Wires Phase 0 -> 1 -> 2 -> 3 together and runs child agents in parallel.

Performance strategy for the 15-30 minute / ~100,000-character-dense
drawing-set target:
- Phase 0 (one small image per sheet) and OCR (CPU-bound, run in a thread
  pool) are cheap and run first so later phases can be scoped down (e.g.
  Phase 1 only runs against tiles from site/plan sheets).
- Phase 2 and Phase 3 child-agent calls are the bulk of the work. They are
  dispatched concurrently across *all* tiles at once; the actual concurrency
  ceiling is enforced by each VLMClient's semaphore
  (``settings.child_vlm.max_concurrency``), not by this module, so raising
  that one number (more GPU headroom / more replicas behind the same
  OpenAI-compatible URL) is the single lever to speed the whole pipeline up.
- Phase 3's ensemble re-reads already happen inside ``detail_agent`` using
  the same concurrency pool, so they parallelize for free rather than
  serializing on top of the tile loop.
- Vector ground-truth extraction (vector_extractor.py) and the downstream
  geometry checks/solid model (solid_model_agent.py) run with zero model
  calls, so they're pure wall-clock overhead on top of I/O -- negligible
  next to the VLM calls -- while measurably raising accuracy (see
  drawing_ai/README.md's accuracy-formation section) and, where they apply,
  let OCR be skipped entirely for a tile (one fewer thing on the critical
  path, not an extra one).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from . import vector_extractor as ve
from .agents import (
    detail_agent,
    intent_agent,
    overview_agent,
    parent_agent,
    site_agent,
    solid_model_agent,
    spec_agent,
    vertical_synthesis_agent,
)
from .config import settings
from .ingestion import load_drawing_set, make_overview_image, tile_sheet
from .ocr_engine import ocr_tile
from .schemas import (
    DimensionReading,
    ElementReading,
    IntentStatement,
    PipelineRun,
    SheetOverview,
    SheetType,
    SiteFact,
    SpecRoom,
    SymbolReading,
    Tile,
)

logger = logging.getLogger("drawing_ai.orchestrator")

# Phase 1 only makes sense against sheets that are plausibly showing site
# info; UNKNOWN is included so a misclassified sheet doesn't silently lose
# its site facts.
SITE_RELEVANT_SHEET_TYPES = {SheetType.SITE_PLAN, SheetType.UNKNOWN}

# COAI-01 (仕様書読解AI) only makes sense against sheets classified as
# specification-like (仕様書・仕上げ表・特記仕様書 etc. -- see
# prompts.OVERVIEW_USER_TEMPLATE for the full alias list); UNKNOWN is
# included for the same "don't silently lose it to misclassification"
# reason as SITE_RELEVANT_SHEET_TYPES above.
SPEC_RELEVANT_SHEET_TYPES = {SheetType.SPECIFICATION, SheetType.UNKNOWN}


async def _gather_bounded(coros: list) -> list:
    """gather() that turns exceptions into logged warnings + None instead of
    aborting the whole batch -- one bad tile must not lose every other
    tile's result."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("child task failed: %s", r)
            out.append(None)
        else:
            out.append(r)
    return out


async def run_pipeline(
    file_paths: list[str],
    work_dir: str,
    *,
    run_llm_reconciliation: bool = True,
) -> PipelineRun:
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    start = time.monotonic()
    work_dir_path = Path(work_dir)
    work_dir_path.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    sheets = load_drawing_set(file_paths, work_dir_path / "sheets")
    logger.info("[%s] loaded %d sheet(s) from %d file(s)", run_id, len(sheets), len(file_paths))

    # --- Phase 0: overview ------------------------------------------------
    overview_images = {
        s.sheet_id: make_overview_image(s, work_dir_path / "overview") for s in sheets
    }
    sheet_overviews: list[SheetOverview] = await _gather_bounded(
        [overview_agent.classify_sheet(s, overview_images[s.sheet_id]) for s in sheets]
    )
    sheet_overviews = [o for o in sheet_overviews if o is not None]
    overview_by_id = {o.sheet_id: o for o in sheet_overviews}

    # --- Vector ground-truth extraction (deterministic, no model calls) ---
    # For a born-digital PDF sheet this pulls the CAD file's own exact text
    # and wall geometry (see vector_extractor.py); for a scanned image or an
    # already-rasterized DWG/DXF/JWW it comes back None and everything below
    # transparently falls back to OCR + AI-only reading.
    ground_truths: dict[str, ve.SheetGroundTruth] = {}
    for sheet in sheets:
        gt = await asyncio.to_thread(ve.extract_ground_truth, sheet)
        if gt is not None:
            ground_truths[sheet.sheet_id] = gt
    logger.info(
        "[%s] vector ground truth available for %d/%d sheet(s)",
        run_id, len(ground_truths), len(sheets),
    )

    # Gross-footprint fallback (deterministic, no model calls): run against
    # *every* sheet with vector ground truth, not just SITE_PLAN-typed
    # ones. A sheet's own wall-fill polygons (SolidModel) may not be
    # extractable (scanned drawing, field-measurement overlay, a CAD
    # vendor's fill convention that doesn't match), and the fact this is
    # meant to recover -- "what is the overall floor area" -- is exactly
    # the kind of information that turns up on whichever sheet happens to
    # carry a clean outer dimension chain, not necessarily the sheet
    # labeled as the site/floor plan. These become additional SiteFact
    # candidates for the same aggregate_phase1() dedup/cross-check pool
    # spec basic-info facts already feed into below.
    footprint_facts: list[SiteFact] = []
    for sheet in sheets:
        gt = ground_truths.get(sheet.sheet_id)
        if gt is None:
            continue
        footprint = ve.estimate_gross_footprint(gt)
        if footprint is None:
            continue
        width_mm, depth_mm, basis = footprint
        area_sqm = (width_mm / 1000.0) * (depth_mm / 1000.0)
        footprint_facts.append(
            SiteFact(
                key="gross_footprint_sqm",
                label_ja="延床面積(概算・グリッド外形基準)",
                value=f"{area_sqm:.1f}",
                unit="m2",
                confidence=0.55,
                source_sheet_ids=[sheet.sheet_id],
                raw_evidence_text=basis,
                needs_human_review=True,
                verified_by_vector=True,
            )
        )

    # --- Tiling + OCR (shared by Phase 1-3) -------------------------------
    all_tiles: list[Tile] = []
    for sheet in sheets:
        tiles = tile_sheet(sheet, work_dir_path / "tiles")
        gt = ground_truths.get(sheet.sheet_id)
        if gt is not None:
            for t in tiles:
                t.ground_truth_text = ve.words_in_bbox(gt, t.x0, t.y0, t.x1, t.y1)
                t.ground_truth_table_rows = ve.extract_table_rows(gt, t.x0, t.y0, t.x1, t.y1)
                t.has_vector_ground_truth = True
        all_tiles.extend(tiles)

    async def _ocr(tile: Tile) -> None:
        # Vector ground truth already gives exact text for this tile; OCR
        # would only add noise on top of it (see README), so it's skipped
        # for tiles that have ground truth and only run where it's needed.
        if tile.has_vector_ground_truth:
            return
        result = await asyncio.to_thread(ocr_tile, tile.image_path)
        tile.ocr_text = result.text

    await _gather_bounded([_ocr(t) for t in all_tiles])
    logger.info("[%s] built %d tile(s) across %d sheet(s)", run_id, len(all_tiles), len(sheets))

    tile_sheet_map = {t.tile_id: t.sheet_id for t in all_tiles}

    def _sheet_type_of(tile: Tile) -> SheetType:
        overview = overview_by_id.get(tile.sheet_id)
        return overview.sheet_type if overview else SheetType.UNKNOWN

    # --- Spec: specification-document reading (COAI-01) ---------------------
    # Run against specification-like sheets (仕様書・仕上げ表・特記仕様書 等),
    # independent of Phase 1-3. The user singled this agent out as the
    # highest-priority accuracy investment -- a well-read spec document
    # narrows roughly 80% of a renovation's construction scope before a
    # single drawing symbol is interpreted -- so its output is folded into
    # Phase 1/2's own pools below rather than kept as an isolated result,
    # letting the spec document's (often higher-fidelity) basic info and
    # intent statements directly raise those phases' accuracy too.
    spec_tiles = [t for t in all_tiles if _sheet_type_of(t) in SPEC_RELEVANT_SHEET_TYPES]
    spec_tile_results = await _gather_bounded([spec_agent.extract_spec(t) for t in spec_tiles])
    spec_tile_results = [r for r in spec_tile_results if r is not None]

    # --- Phase 1: site facts ------------------------------------------------
    site_tiles = [t for t in all_tiles if _sheet_type_of(t) in SITE_RELEVANT_SHEET_TYPES]
    site_fact_lists: list[list[SiteFact]] = await _gather_bounded(
        [site_agent.extract_site_facts(t) for t in site_tiles]
    )
    all_site_facts = [f for group in site_fact_lists if group for f in group]
    for spec_result_tile in spec_tile_results:
        all_site_facts.extend(spec_result_tile.basic_facts)
    all_site_facts.extend(footprint_facts)
    phase1 = parent_agent.aggregate_phase1(all_site_facts)
    logger.info(
        "[%s] phase1: %d fact(s), completeness=%.2f", run_id, len(phase1.facts), phase1.completeness_score
    )

    # --- Phase 2: intent ------------------------------------------------
    intent_results = await _gather_bounded([intent_agent.extract_intent(t) for t in all_tiles])
    statements: list[IntentStatement] = [s for s in intent_results if s is not None]
    for spec_result_tile in spec_tile_results:
        statements.extend(spec_result_tile.intent_statements)
    project_summary = await parent_agent.summarize_project(statements)
    phase2 = parent_agent.aggregate_phase2(statements, project_summary_ja=project_summary)
    logger.info("[%s] phase2: %d intent statement(s)", run_id, len(phase2.statements))

    # --- Spec aggregation (COAI-01 detailed room/spec output) ---------------
    all_spec_rooms: list[SpecRoom] = [room for r in spec_tile_results for room in r.rooms]
    all_scope_terms = [term for r in spec_tile_results for term in r.scope_target_terms]
    spec = parent_agent.aggregate_spec(all_spec_rooms, scope_target_terms=all_scope_terms)
    logger.info(
        "[%s] spec: %d room(s), %d ambiguous flag(s) -- %s",
        run_id, len(spec.rooms), len(spec.ambiguous_flags), spec.completeness_note,
    )

    # --- Phase 3: precise detail reading ------------------------------------
    detail_results = await _gather_bounded([detail_agent.extract_details(t) for t in all_tiles])
    all_dimensions: list[DimensionReading] = []
    all_symbols: list[SymbolReading] = []
    all_elements: list[ElementReading] = []
    for result in detail_results:
        if result is None:
            continue
        dims, syms, elems = result
        all_dimensions.extend(dims)
        all_symbols.extend(syms)
        all_elements.extend(elems)

    section_dim_tiles = {
        t.tile_id for t in all_tiles if _sheet_type_of(t) in (SheetType.ELEVATION, SheetType.SECTION)
    }
    section_dimensions = [d for d in all_dimensions if d.tile_id in section_dim_tiles] or all_dimensions

    vertical_notes = await vertical_synthesis_agent.synthesize_vertical(
        sheet_overviews, all_elements, section_dimensions
    )

    phase3 = await parent_agent.aggregate_phase3(
        all_dimensions,
        all_symbols,
        all_elements,
        vertical_notes,
        tile_sheet_map,
        use_llm_reconciliation=run_llm_reconciliation,
    )

    # --- Deterministic geometry layer (no model calls) ----------------------
    # Dimension-chain arithmetic self-check (加算検算) and the solid wall/
    # ceiling-height model both run purely off the vector ground truth
    # extracted above -- independent of, and a cross-check against, every
    # AI-derived reading above.
    for gt in ground_truths.values():
        phase3.dimension_chain_flags.extend(ve.check_dimension_chains(gt))

    tiles_by_id = {t.tile_id: t for t in all_tiles}
    phase3.solid_model = solid_model_agent.build_solid_model(ground_truths, phase3.elements, tiles_by_id)

    logger.info(
        "[%s] phase3: %d dimension(s), %d symbol(s), %d element(s), accuracy_estimate=%.2f, "
        "%d dimension-chain flag(s), %d wall segment(s)",
        run_id,
        len(phase3.dimensions),
        len(phase3.symbols),
        len(phase3.elements),
        phase3.accuracy_estimate,
        len(phase3.dimension_chain_flags),
        len(phase3.solid_model.wall_segments),
    )

    elapsed = time.monotonic() - start
    if elapsed > settings.time_budget_s:
        warnings.append(
            f"処理時間が目標({settings.time_budget_s}秒)を超過しました: {elapsed:.0f}秒。"
            "child_vlm.max_concurrencyの引き上げ、またはタイルサイズの見直しを検討してください。"
        )
    elif elapsed > settings.time_budget_target_s:
        warnings.append(
            f"処理時間が目安({settings.time_budget_target_s}秒)を超えています: {elapsed:.0f}秒。"
        )

    model_calls = (
        len(sheets)  # phase0
        + len(spec_tiles) * max(1, settings.spec_ensemble_size)  # spec (COAI-01)
        + len(site_tiles) * max(1, settings.phase1_ensemble_size)  # phase1
        + len(all_tiles)  # phase2
        + len(all_tiles) * max(1, settings.phase3_ensemble_size)  # phase3
        + (1 if statements else 0)  # project summary
        + (1 if run_llm_reconciliation else 0)  # phase3 reconciliation
        + (1 if vertical_notes else 0)
    )

    return PipelineRun(
        run_id=run_id,
        sheets=sheet_overviews,
        spec=spec,
        phase1=phase1,
        phase2=phase2,
        phase3=phase3,
        elapsed_s=elapsed,
        tile_count=len(all_tiles),
        model_calls=model_calls,
        warnings=warnings,
    )
