"""Phase 1: 敷地情報 (site/lot facts). Target: 100% accuracy.

Only run against tiles from sheets classified as SITE_PLAN (or UNKNOWN, as a
safety net) by the Phase 0 overview pass -- there is no point asking a
floor-plan tile about setback distances.

Like Phase 3, each tile is read independently
``settings.phase1_ensemble_size`` times and reconciled locally: Phase 1's
accuracy bar is the highest in the whole pipeline (the user's explicit
target is 100%), so it gets the same self-consistency + grounding treatment
as Phase 3 rather than trusting a single pass.
"""
from __future__ import annotations

import asyncio
import logging

from .. import grounding
from ..config import settings
from ..prompts import SITE_SYSTEM_PROMPT, SITE_USER_TEMPLATE
from ..schemas import SiteFact, Tile
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.site")

# Below this, a fact is flagged for human confirmation rather than trusted
# outright -- Phase 1's accuracy bar is high enough that "probably right" is
# not good enough on its own.
CONFIDENCE_REVIEW_THRESHOLD = 0.8


async def _one_pass(tile: Tile) -> list[dict]:
    client = get_client(settings.child_vlm)
    prompt = SITE_USER_TEMPLATE.format(ocr_text=grounding.grounding_context(tile)[:2000])
    response = await client.chat(
        SITE_SYSTEM_PROMPT,
        prompt,
        image_paths=[tile.image_path],
        max_tokens=900,
    )
    data = extract_json(response.text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of site facts")
    return data


def _reconcile(passes: list[list[dict]], tile: Tile) -> list[SiteFact]:
    seen: dict[tuple[str, str], list[dict]] = {}
    for pass_items in passes:
        for item in pass_items:
            key = (str(item.get("key", "other")), str(item.get("value", "")).strip())
            if not key[1]:
                continue
            seen.setdefault(key, []).append(item)

    facts: list[SiteFact] = []
    for (fact_key, raw_value), items in seen.items():
        best = max(items, key=lambda it: float(it.get("confidence", 0.0)))
        # Independent agreement across ensemble passes is exactly the kind
        # of redundancy the 100%-accuracy target calls for -- weighted
        # slightly higher than Phase 3's, since Phase 1 has no downstream
        # phase left to catch a mistake.
        agreement_bonus = 0.2 * (len(items) - 1) if len(items) > 1 else 0.0

        delta, verified = grounding.numeric_adjustment(raw_value, tile)
        if not verified and delta == 0.0:
            delta, verified = grounding.text_adjustment(raw_value, tile)

        confidence = max(0.0, min(1.0, float(best.get("confidence", 0.0)) + agreement_bonus + delta))
        facts.append(
            SiteFact(
                key=fact_key,
                label_ja=str(best.get("label_ja", "")),
                value=raw_value,
                unit=best.get("unit"),
                confidence=confidence,
                source_tile_ids=[tile.tile_id],
                source_sheet_ids=[tile.sheet_id],
                raw_evidence_text=str(best.get("raw_evidence_text", "")),
                needs_human_review=confidence < CONFIDENCE_REVIEW_THRESHOLD,
                verified_by_vector=verified,
            )
        )
    return facts


async def extract_site_facts(tile: Tile) -> list[SiteFact]:
    ensemble_size = max(1, settings.phase1_ensemble_size)
    results = await asyncio.gather(*(_one_pass(tile) for _ in range(ensemble_size)), return_exceptions=True)

    passes: list[list[dict]] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("site fact extraction pass failed for tile %s: %s", tile.tile_id, result)
            continue
        passes.append(result)

    if not passes:
        return []
    return _reconcile(passes, tile)
