"""Combine plan-view elements with elevation/section dimensions.

This is the explicit "3D in the head" step the user described: a human
estimator takes the flat, room-by-room plan information and the vertical
(height) information from elevations/sections and mentally stacks them into
one building. We do not build real 3D geometry here -- the output is a
per-room/zone text+number summary -- but it is produced as its own
reasoning step (a dedicated LLM call over already-structured text, not
pixels) rather than being silently skipped, because skipping it is exactly
where naive plan-only pipelines lose the information needed to sequence
trades (walls before ceiling height finishes, etc.) in Phase 4.
"""
from __future__ import annotations

import json
import logging

from ..config import settings
from ..prompts import VERTICAL_SYNTHESIS_SYSTEM_PROMPT, VERTICAL_SYNTHESIS_USER_TEMPLATE
from ..schemas import DimensionReading, ElementReading, SheetOverview, SheetType, VerticalSynthesisNote
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.vertical_synthesis")


async def synthesize_vertical(
    sheets: list[SheetOverview],
    elements: list[ElementReading],
    dimensions: list[DimensionReading],
) -> list[VerticalSynthesisNote]:
    plan_sheet_ids = {s.sheet_id for s in sheets if s.sheet_type == SheetType.FLOOR_PLAN}
    section_sheet_ids = {
        s.sheet_id for s in sheets if s.sheet_type in (SheetType.ELEVATION, SheetType.SECTION)
    }

    plan_elements = [e for e in elements if e.sheet_id in plan_sheet_ids]
    # DimensionReading only carries tile_id, not sheet_id, so the orchestrator
    # is responsible for pre-filtering `dimensions` to section/elevation tiles
    # before calling this function when that distinction matters.
    if not plan_elements and not dimensions:
        return []

    client = get_client(settings.parent_llm)
    prompt = VERTICAL_SYNTHESIS_USER_TEMPLATE.format(
        plan_elements_json=json.dumps(
            [e.model_dump() for e in plan_elements[:60]], ensure_ascii=False
        ),
        section_dimensions_json=json.dumps(
            [d.model_dump() for d in dimensions[:60]], ensure_ascii=False
        ),
    )

    try:
        response = await client.chat(
            VERTICAL_SYNTHESIS_SYSTEM_PROMPT,
            prompt,
            image_paths=None,
            max_tokens=1200,
        )
        data = extract_json(response.text)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array of vertical synthesis notes")
        notes: list[VerticalSynthesisNote] = []
        for item in data:
            notes.append(
                VerticalSynthesisNote(
                    room_or_zone=str(item.get("room_or_zone", "")),
                    floor_height_m=_as_float(item.get("floor_height_m")),
                    wall_height_m=_as_float(item.get("wall_height_m")),
                    notes_ja=str(item.get("notes_ja", "")),
                    source_sheet_ids=list(plan_sheet_ids | section_sheet_ids),
                    confidence=float(item.get("confidence", 0.0)),
                )
            )
        return notes
    except (VLMCallError, ValueError, KeyError) as exc:
        logger.warning("vertical synthesis failed: %s", exc)
        return []


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
