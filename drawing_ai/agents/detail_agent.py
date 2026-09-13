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
