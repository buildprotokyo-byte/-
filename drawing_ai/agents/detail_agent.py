"""Phase 3: read numbers/lines/symbols precisely and interpret their meaning.

This is the highest-stakes phase, so each tile is read independently
``settings.phase3_ensemble_size`` times (ensemble / self-consistency) and the
results are reconciled locally before being handed up to the parent agent:
- a dimension reading that agrees (same raw_text, close value) across passes
  has its confidence boosted and is kept once
- a reading that only shows up in one pass is kept but not boosted
- readings are additionally cross-checked against the tile's best available
  text-grounding source. When the sheet is a born-digital PDF, that source
  is exact text pulled from the CAD file itself (see vector_extractor.py)
  and disagreement is treated as a strong hallucination signal (-0.5);
  otherwise it falls back to OCR, which is far less reliable (confirmed on
  a real sample drawing -- see README), so the adjustment there stays a
  gentle +0.1 / -0.2.
"""
from __future__ import annotations

import asyncio
import logging

from .. import grounding
from ..area_parsing import parse_area_text
from ..config import settings
from ..prompts import DETAIL_SYSTEM_PROMPT, DETAIL_USER_TEMPLATE
from ..schemas import DimensionReading, ElementReading, SymbolReading, Tile
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.detail")

_normalize_number = grounding.normalize_number


async def _one_pass(tile: Tile) -> dict:
    client = get_client(settings.child_vlm)
    prompt = DETAIL_USER_TEMPLATE.format(ocr_text=grounding.grounding_context(tile)[:2000])
    response = await client.chat(
        DETAIL_SYSTEM_PROMPT,
        prompt,
        image_paths=[tile.image_path],
        temperature=0.2,
        max_tokens=1400,
    )
    data = extract_json(response.text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object with dimensions/symbols/elements")
    return data


def _reconcile_dimensions(passes: list[list[dict]], tile: Tile) -> list[DimensionReading]:
    seen: dict[str, list[dict]] = {}
    for pass_items in passes:
        for item in pass_items:
            key = _normalize_number(str(item.get("raw_text", "")))
            if not key:
                continue
            seen.setdefault(key, []).append(item)

    out: list[DimensionReading] = []
    for key, items in seen.items():
        best = max(items, key=lambda it: float(it.get("confidence", 0.0)))
        agreement_bonus = 0.15 * (len(items) - 1) if len(items) > 1 else 0.0
        grounding_delta, verified = grounding.numeric_adjustment(str(best.get("raw_text", "")), tile)
        confidence = max(0.0, min(1.0, float(best.get("confidence", 0.0)) + agreement_bonus + grounding_delta))
        value = best.get("value")
        out.append(
            DimensionReading(
                tile_id=tile.tile_id,
                raw_text=str(best.get("raw_text", "")),
                value=float(value) if isinstance(value, (int, float)) else None,
                unit=best.get("unit"),
                role_ja=str(best.get("role_ja", "")),
                associated_elements=[str(e) for e in best.get("associated_elements", [])],
                confidence=confidence,
                verified_by_vector=verified,
            )
        )
    return out


def _reconcile_symbols(passes: list[list[dict]], tile: Tile) -> list[SymbolReading]:
    seen: dict[str, list[dict]] = {}
    for pass_items in passes:
        for item in pass_items:
            key = str(item.get("symbol_text_or_glyph", "")).strip()
            if not key:
                continue
            seen.setdefault(key, []).append(item)

    out: list[SymbolReading] = []
    for key, items in seen.items():
        best = max(items, key=lambda it: float(it.get("confidence", 0.0)))
        agreement_bonus = 0.1 * (len(items) - 1) if len(items) > 1 else 0.0
        grounding_delta, verified = grounding.text_adjustment(key, tile)
        confidence = max(0.0, min(1.0, float(best.get("confidence", 0.0)) + agreement_bonus + grounding_delta))
        out.append(
            SymbolReading(
                tile_id=tile.tile_id,
                symbol_text_or_glyph=key,
                meaning_ja=str(best.get("meaning_ja", "")),
                location_hint=str(best.get("location_hint", "")),
                confidence=confidence,
                verified_by_vector=verified,
            )
        )
    return out


def _estimate_area_from_width_depth(item: dict, tile: Tile) -> tuple[float | None, str]:
    """Area from a VLM-read width/depth pair -- the fallback tier used only
    when no printed area label exists (see area_text/parse_area_text
    above). This is deliberately a *visual* judgment call (the model
    decides which two numbers on the page belong to this room's two
    axes), not a text-position heuristic -- vector_extractor's
    dimension-chain clustering only finds CAD-generated, collinear
    dimension rows, and a real sample project (a field-measurement
    annotation overlay, each number written individually next to its own
    wall segment with no shared baseline) showed that assumption fails
    outright on scattered, hand-placed annotations.

    Because this leans on the model's own judgment rather than exact
    positions, both numbers are required to be grounding-verified against
    the tile's own CAD-native ground-truth text before being trusted --
    mirroring vector_extractor's documented refusal to publish an
    unreliable geometric check rather than a flaky one. When the tile has
    no CAD-native ground truth (either no vector text at all, or only an
    OCR text layer over a scanned image -- see
    grounding.is_cad_native/Tile.ground_truth_trust_tier -- which is
    exact-match-unreliable enough that requiring it to match would just
    as often wrongly reject a correct reading as catch a hallucinated
    one), verification isn't possible either way, so the reading is
    accepted at face value (same treatment as any other ungrounded
    reading elsewhere in this pipeline).
    """
    width_text = str(item.get("width_text", "")).strip()
    depth_text = str(item.get("depth_text", "")).strip()
    if not width_text or not depth_text:
        return None, "none"

    try:
        width_mm = float(_normalize_number(width_text))
        depth_mm = float(_normalize_number(depth_text))
    except ValueError:
        return None, "none"
    if not width_mm or not depth_mm:
        return None, "none"

    if grounding.is_cad_native(tile):
        _, width_verified = grounding.numeric_adjustment(width_text, tile)
        _, depth_verified = grounding.numeric_adjustment(depth_text, tile)
        if not (width_verified and depth_verified):
            return None, "none"

    return round((width_mm / 1000.0) * (depth_mm / 1000.0), 2), "width_depth_estimate"


def _reconcile_elements(passes: list[list[dict]], tile: Tile) -> list[ElementReading]:
    seen: dict[tuple[str, str], list[dict]] = {}
    for pass_items in passes:
        for item in pass_items:
            key = (str(item.get("element_type", "other")), str(item.get("label_ja", "")).strip())
            if not key[1]:
                continue
            seen.setdefault(key, []).append(item)

    out: list[ElementReading] = []
    for (element_type, label_ja), items in seen.items():
        best = max(items, key=lambda it: float(it.get("confidence", 0.0)))
        agreement_bonus = 0.1 * (len(items) - 1) if len(items) > 1 else 0.0
        grounding_delta, verified = grounding.text_adjustment(label_ja, tile)
        confidence = max(0.0, min(1.0, float(best.get("confidence", 0.0)) + agreement_bonus + grounding_delta))

        area_sqm = None
        area_source = "none"
        parsed = parse_area_text(str(best.get("area_text", "")))
        if parsed is not None:
            area_sqm, area_source = parsed
        else:
            area_sqm, area_source = _estimate_area_from_width_depth(best, tile)

        out.append(
            ElementReading(
                element_id=f"{tile.tile_id}-{element_type}-{len(out)}",
                element_type=element_type,
                label_ja=label_ja,
                sheet_id=tile.sheet_id,
                tile_ids=[tile.tile_id],
                attributes={str(k): str(v) for k, v in dict(best.get("attributes", {})).items()},
                confidence=confidence,
                verified_by_vector=verified,
                area_sqm=area_sqm,
                area_source=area_source,
            )
        )
    return out


async def extract_details(tile: Tile) -> tuple[list[DimensionReading], list[SymbolReading], list[ElementReading]]:
    ensemble_size = max(1, settings.phase3_ensemble_size)
    results = await asyncio.gather(
        *(_one_pass(tile) for _ in range(ensemble_size)), return_exceptions=True
    )

    dim_passes, sym_passes, elem_passes = [], [], []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("detail pass failed for tile %s: %s", tile.tile_id, result)
            continue
        dim_passes.append(result.get("dimensions", []) or [])
        sym_passes.append(result.get("symbols", []) or [])
        elem_passes.append(result.get("elements", []) or [])

    if not dim_passes and not sym_passes and not elem_passes:
        return [], [], []

    return (
        _reconcile_dimensions(dim_passes, tile),
        _reconcile_symbols(sym_passes, tile),
        _reconcile_elements(elem_passes, tile),
    )
