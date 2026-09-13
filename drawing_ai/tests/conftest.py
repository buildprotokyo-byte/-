"""Shared test fixtures.

No test in this package makes a real network call. Every VLM interaction is
stubbed with :class:`FakeVLMClient`, which returns canned, schema-shaped
JSON keyed off a keyword found in the system prompt each agent module uses.
This lets the whole orchestration/reconciliation logic (tiling, dedup,
confidence scoring, phase sequencing) be exercised deterministically without
a GPU or a running model server.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest


@dataclass
class FakeResponse:
    text: str


class FakeVLMClient:
    """Drop-in stand-in for drawing_ai.vlm_client.VLMClient."""

    def __init__(self, dispatch: dict[str, object]) -> None:
        self.dispatch = dispatch
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system_prompt: str, user_text: str, image_paths=None, **kwargs):
        self.calls.append((system_prompt, user_text))
        for keyword, payload in self.dispatch.items():
            if keyword in system_prompt:
                text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                return FakeResponse(text=text)
        raise AssertionError(f"no fake response registered for system prompt: {system_prompt[:60]!r}")


DEFAULT_DISPATCH = {
    "一次確認担当": {
        "sheet_type": "site_plan",
        "sheet_type_confidence": 0.9,
        "title_block_text": "配置図 S=1/100",
        "one_line_summary": "木造2階建て住宅の配置図",
        "scale_hint": "1/100",
        "notes": "",
    },
    "敷地条件": [
        {
            "key": "site_area_sqm",
            "label_ja": "敷地面積",
            "value": "165.29",
            "unit": "m2",
            "confidence": 0.92,
            "raw_evidence_text": "165.29m2",
        }
    ],
    "何を実現したいか": {
        "text_ja": "既存の和室を洋室に改装し、床をフローリングに変更する。",
        "construction_categories": ["大工工事", "内装仕上工事"],
        "confidence": 0.8,
    },
    "寸法数字・線・記号": {
        "dimensions": [
            {
                "raw_text": "2,730",
                "value": 2730,
                "unit": "mm",
                "role_ja": "壁芯-芯距離",
                "associated_elements": ["リビング"],
                "confidence": 0.85,
            }
        ],
        "symbols": [
            {
                "symbol_text_or_glyph": "WC",
                "meaning_ja": "コンセント",
                "location_hint": "中央付近",
                "confidence": 0.7,
            }
        ],
        "elements": [
            {
                "element_type": "room",
                "label_ja": "リビング",
                "attributes": {"面積": "12.5畳"},
                "confidence": 0.75,
            }
        ],
    },
    "平面図(横方向の配置)と立面図": [
        {
            "room_or_zone": "リビング",
            "floor_height_m": 0.45,
            "wall_height_m": 2.4,
            "notes_ja": "平面図の壁位置と立面図の階高を突き合わせて推定",
            "confidence": 0.6,
        }
    ],
    "案件概要文をまとめる担当": "本件は既存木造住宅の内装改修工事であり、和室の洋室化とフローリング張替えを含む。",
    "子AIが図面の断片ごとに読み取った結果を統合": {
        "dimensions": [
            {
                "tile_id": "TILE",
                "raw_text": "2,730",
                "value": 2730,
                "unit": "mm",
                "role_ja": "壁芯-芯距離",
                "associated_elements": ["リビング"],
                "confidence": 0.9,
            }
        ],
        "symbols": [
            {
                "tile_id": "TILE",
                "symbol_text_or_glyph": "WC",
                "meaning_ja": "コンセント",
                "location_hint": "中央付近",
                "confidence": 0.7,
            }
        ],
        "elements": [
            {
                "element_id": "e1",
                "element_type": "room",
                "label_ja": "リビング",
                "sheet_id": "sheet",
                "tile_ids": ["TILE"],
                "attributes": {"面積": "12.5畳"},
                "confidence": 0.75,
            }
        ],
        "low_confidence_flags": [],
        "accuracy_estimate": 0.8,
    },
}


@pytest.fixture
def fake_clients(monkeypatch):
    """Patch every agent module's ``get_client`` to return one shared fake."""
    client = FakeVLMClient(DEFAULT_DISPATCH)

    def _get_client(_endpoint):
        return client

    for modpath in [
        "drawing_ai.agents.overview_agent",
        "drawing_ai.agents.site_agent",
        "drawing_ai.agents.intent_agent",
        "drawing_ai.agents.detail_agent",
        "drawing_ai.agents.vertical_synthesis_agent",
        "drawing_ai.agents.parent_agent",
    ]:
        monkeypatch.setattr(f"{modpath}.get_client", _get_client)

    return client
