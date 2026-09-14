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


def build_synthetic_vector_pdf(path: str) -> None:
    """A tiny born-digital PDF (real text + a real filled wall path) shared
    by vector_extractor and end-to-end pipeline tests, so both exercise the
    same ground-truth extraction code path without any external file."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((500, 380), "縮尺", fontsize=8)
    page.insert_text((520, 380), "1/50", fontsize=8)
    page.insert_text((250, 150), "CH=2500", fontsize=8)
    page.insert_text((300, 200), "2730", fontsize=8)

    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(300, 250, 340, 290))
    shape.finish(fill=(0.494, 0.463, 0.447), color=None)
    shape.commit()

    doc.save(path)
    doc.close()


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
    "基本仕様書": {
        "basic_info_facts": [
            {
                "key": "building_structure",
                "label_ja": "建物構造",
                "value": "木造2階建て",
                "unit": None,
                "confidence": 0.9,
                "raw_evidence_text": "木造2階建て",
            }
        ],
        "scope_target_terms": ["2階居室のみ"],
        "desired_change_statements": [
            {
                "text_ja": "和室を洋室に改装する。",
                "construction_categories": ["大工工事"],
                "confidence": 0.8,
            }
        ],
        "rooms": [
            {
                "room_name": "洋室A",
                "specs": [
                    {
                        "finish": "CL",
                        "substrate": "石膏ボード",
                        "substrate_status": "new",
                        "notes": "",
                        "in_scope": True,
                        "confidence": 0.85,
                        "product": {
                            "manufacturer": None,
                            "model_number": None,
                            "category": "",
                            "looked_up_details": "",
                            "lookup_status": "not_attempted",
                        },
                    }
                ],
            }
        ],
    },
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
        "drawing_ai.agents.spec_agent",
    ]:
        monkeypatch.setattr(f"{modpath}.get_client", _get_client)

    return client
