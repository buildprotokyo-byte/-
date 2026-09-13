"""Phase 1: 敷地情報 (site/lot facts). Target: 100% accuracy.

Only run against tiles from sheets classified as SITE_PLAN (or UNKNOWN, as a
safety net) by the Phase 0 overview pass -- there is no point asking a
floor-plan tile about setback distances.
"""
from __future__ import annotations

import logging

from ..config import settings
from ..prompts import SITE_SYSTEM_PROMPT, SITE_USER_TEMPLATE
from ..schemas import SiteFact, Tile
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.site")

# Below this, a fact is flagged for human confirmation rather than trusted
# outright -- Phase 1's accuracy bar is high enough that "probably right" is
# not good enough on its own.
CONFIDENCE_REVIEW_THRESHOLD = 0.8


async def extract_site_facts(tile: Tile) -> list[SiteFact]:
    client = get_client(settings.child_vlm)
    prompt = SITE_USER_TEMPLATE.format(ocr_text=tile.ocr_text[:2000])

    try:
        response = await client.chat(
            SITE_SYSTEM_PROMPT,
            prompt,
            image_paths=[tile.image_path],
            max_tokens=900,
        )
        data = extract_json(response.text)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array of site facts")

        facts: list[SiteFact] = []
        for item in data:
            confidence = float(item.get("confidence", 0.0))
            facts.append(
                SiteFact(
                    key=str(item.get("key", "other")),
                    label_ja=str(item.get("label_ja", "")),
                    value=str(item.get("value", "")),
                    unit=item.get("unit"),
                    confidence=confidence,
                    source_tile_ids=[tile.tile_id],
                    source_sheet_ids=[tile.sheet_id],
                    raw_evidence_text=str(item.get("raw_evidence_text", "")),
                    needs_human_review=confidence < CONFIDENCE_REVIEW_THRESHOLD,
                )
            )
        return facts
    except (VLMCallError, ValueError, KeyError) as exc:
        logger.warning("site fact extraction failed for tile %s: %s", tile.tile_id, exc)
        return []
