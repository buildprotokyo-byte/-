"""Phase 0: the human 'first glance' pass -- one call per sheet."""
from __future__ import annotations

import logging

from ..config import settings
from ..ingestion import RenderedSheet
from ..prompts import OVERVIEW_SYSTEM_PROMPT, OVERVIEW_USER_TEMPLATE
from ..schemas import SheetOverview, SheetType
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.overview")


async def classify_sheet(sheet: RenderedSheet, overview_image_path: str, ocr_text: str = "") -> SheetOverview:
    client = get_client(settings.child_vlm)
    prompt = OVERVIEW_USER_TEMPLATE.format(ocr_text=ocr_text[:2000])

    try:
        response = await client.chat(
            OVERVIEW_SYSTEM_PROMPT,
            prompt,
            image_paths=[overview_image_path],
            max_tokens=500,
        )
        data = extract_json(response.text)
        sheet_type = SheetType(data.get("sheet_type", "unknown"))
        return SheetOverview(
            sheet_id=sheet.sheet_id,
            sheet_index=sheet.sheet_index,
            sheet_type=sheet_type,
            sheet_type_confidence=float(data.get("sheet_type_confidence", 0.0)),
            title_block_text=str(data.get("title_block_text", "")),
            one_line_summary=str(data.get("one_line_summary", "")),
            scale_hint=data.get("scale_hint"),
            notes=str(data.get("notes", "")),
        )
    except (VLMCallError, ValueError, KeyError) as exc:
        logger.warning("overview classification failed for %s: %s", sheet.sheet_id, exc)
        return SheetOverview(
            sheet_id=sheet.sheet_id,
            sheet_index=sheet.sheet_index,
            sheet_type=SheetType.UNKNOWN,
            sheet_type_confidence=0.0,
            notes=f"自動分類に失敗しました: {exc}",
        )
