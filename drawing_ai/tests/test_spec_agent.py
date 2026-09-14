from __future__ import annotations

import pytest

from drawing_ai import terminology
from drawing_ai.agents import parent_agent, spec_agent
from drawing_ai.schemas import SpecRoom, Tile


def _tile(ground_truth_text: str = "", table_rows: list[list[str]] | None = None) -> Tile:
    return Tile(
        tile_id="t1",
        sheet_id="s1",
        sheet_index=0,
        row=0,
        col=0,
        x0=0,
        y0=0,
        x1=100,
        y1=100,
        image_path="unused.png",
        ground_truth_text=ground_truth_text,
        has_vector_ground_truth=bool(ground_truth_text or table_rows),
        ground_truth_table_rows=table_rows or [],
    )


def test_normalize_term_maps_known_variants_and_abbreviations():
    assert terminology.normalize_term("CL") == "ビニールクロス張り"
    assert terminology.normalize_term("クロス張り") == "ビニールクロス張り"
    assert terminology.normalize_term("ビニールクロス") == "ビニールクロス張り"


def test_normalize_term_returns_none_for_unknown_term():
    assert terminology.normalize_term("特殊左官仕上げXYZ") is None


def test_make_spec_item_normalizes_and_grounds():
    tile = _tile(ground_truth_text="洋室A CL 石膏ボード新規")
    raw = {
        "finish": "CL",
        "substrate": "石膏ボード",
        "substrate_status": "new",
        "confidence": 0.6,
        "product": {"manufacturer": None, "model_number": None, "category": "", "lookup_status": "not_attempted"},
    }
    item = spec_agent._make_spec_item(raw, tile)
    assert item.raw_finish_term == "CL"
    assert item.canonical_finish_term == "ビニールクロス張り"
    assert item.finish == "ビニールクロス張り"
    # "CL" is inside the tile's ground-truth text -> text_adjustment grounds it.
    assert item.verified_by_vector is True
    assert item.confidence > 0.6


def test_make_spec_item_keeps_raw_term_when_unmapped():
    tile = _tile()
    raw = {"finish": "特殊仕上げXYZ", "confidence": 0.5}
    item = spec_agent._make_spec_item(raw, tile)
    assert item.canonical_finish_term is None
    assert item.finish == "特殊仕上げXYZ"


def test_reconcile_rooms_boosts_confidence_on_ensemble_agreement():
    tile = _tile()
    passes = [
        [{"room_name": "洋室A", "specs": [{"finish": "CL", "substrate": "PB", "confidence": 0.6}]}],
        [{"room_name": "洋室A", "specs": [{"finish": "CL", "substrate": "PB", "confidence": 0.65}]}],
    ]
    rooms = spec_agent._reconcile_rooms(passes, tile)
    assert len(rooms) == 1
    assert len(rooms[0].specs) == 1
    # base 0.65 (higher of the two) + 0.1 agreement bonus for the second pass.
    assert rooms[0].specs[0].confidence > 0.65


def test_reconcile_rooms_keeps_distinct_finishes_separate():
    tile = _tile()
    passes = [
        [
            {
                "room_name": "洋室A",
                "specs": [
                    {"finish": "CL", "substrate": "PB", "confidence": 0.7},
                    {"finish": "フローリング", "substrate": "合板", "confidence": 0.7},
                ],
            }
        ]
    ]
    rooms = spec_agent._reconcile_rooms(passes, tile)
    assert len(rooms) == 1
    assert {s.raw_finish_term for s in rooms[0].specs} == {"CL", "フローリング"}


@pytest.mark.asyncio
async def test_extract_spec_uses_ensemble_and_folds_fixed_questions(monkeypatch):
    from drawing_ai.config import settings
    from drawing_ai.tests.conftest import DEFAULT_DISPATCH, FakeVLMClient

    monkeypatch.setattr(settings, "spec_ensemble_size", 2)
    client = FakeVLMClient(DEFAULT_DISPATCH)
    monkeypatch.setattr(spec_agent, "get_client", lambda _endpoint: client)

    tile = _tile()
    result = await spec_agent.extract_spec(tile)

    assert len(client.calls) == 2  # ensemble ran twice
    assert result.rooms and result.rooms[0].room_name == "洋室A"
    assert result.basic_facts and result.basic_facts[0].key == "building_structure"
    assert result.scope_target_terms == ["2階居室のみ"]
    assert result.intent_statements and "洋室" in result.intent_statements[0].text_ja


def test_aggregate_spec_merges_rooms_across_tiles():
    room_a = SpecRoom(room_name="洋室A", source_sheet_ids=["s1"])
    room_a.specs.append(
        spec_agent._make_spec_item({"finish": "CL", "substrate": "PB", "substrate_status": "new", "confidence": 0.7}, _tile())
    )
    room_a_2 = SpecRoom(room_name="洋室A", source_sheet_ids=["s1"])
    room_a_2.specs.append(
        spec_agent._make_spec_item(
            {"finish": "フローリング", "substrate": "合板", "substrate_status": "new", "confidence": 0.7}, _tile()
        )
    )
    result = parent_agent.aggregate_spec([room_a, room_a_2], scope_target_terms=["2階居室のみ", "2階居室のみ"])
    assert len(result.rooms) == 1
    assert len(result.rooms[0].specs) == 2
    assert result.scope_target_terms == ["2階居室のみ"]  # deduped


def test_aggregate_spec_flags_substrate_status_conflict():
    room_new = SpecRoom(room_name="洋室A", source_sheet_ids=["s1"])
    room_new.specs.append(
        spec_agent._make_spec_item({"finish": "CL", "substrate": "PB", "substrate_status": "new", "confidence": 0.6}, _tile())
    )
    room_existing = SpecRoom(room_name="洋室A", source_sheet_ids=["s2"])
    room_existing.specs.append(
        spec_agent._make_spec_item(
            {"finish": "CL", "substrate": "PB", "substrate_status": "existing", "confidence": 0.9}, _tile()
        )
    )
    result = parent_agent.aggregate_spec([room_new, room_existing])
    assert result.ambiguous_flags
    assert "洋室A" in result.ambiguous_flags[0]
    # the higher-confidence reading (existing, 0.9) wins the merged slot
    assert result.rooms[0].specs[0].substrate_status == "existing"
