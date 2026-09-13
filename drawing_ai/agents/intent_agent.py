"""Phase 2: read what the drawing is asking for, and phrase it as text.

This is the "何がやりたいかを読み取り、それを見積もりの文章にする" step. Each
tile produces at most one intent statement; the orchestrator later asks the
parent agent to fold per-sheet statements into one project-level summary.
"""
from __future__ import annotations

import logging

from ..config import settings
from ..prompts import INTENT_SYSTEM_PROMPT, INTENT_USER_TEMPLATE
from ..schemas import IntentStatement, Tile
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.intent")


async def extract_intent(tile: Tile) -> IntentStatement | None:
    client = get_client(settings.child_vlm)
    prompt = INTENT_USER_TEMPLATE.format(ocr_text=tile.ocr_text[:2000])

    try:
        response = await client.chat(
            INTENT_SYSTEM_PROMPT,
            prompt,
            image_paths=[tile.image_path],
            max_tokens=500,
        )
        data = extract_json(response.text)
        text_ja = str(data.get("text_ja", "")).strip()
        if not text_ja:
            return None
        return IntentStatement(
            sheet_id=tile.sheet_id,
            text_ja=text_ja,
            construction_categories=[str(c) for c in data.get("construction_categories", [])],
            confidence=float(data.get("confidence", 0.0)),
            source_tile_ids=[tile.tile_id],
        )
    except (VLMCallError, ValueError, KeyError) as exc:
        logger.warning("intent extraction failed for tile %s: %s", tile.tile_id, exc)
        return None
