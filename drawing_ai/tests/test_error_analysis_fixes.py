"""Tests for the fixes made after the KDX802 blind-test verification report.

Each test is anchored to a specific, named root cause from that report
(see VERIFICATION_REPORT_KDX802.md) rather than a generic case, and where
possible uses the real numbers found in that project so these tests would
have actually caught the original mistake.
"""
from __future__ import annotations

from drawing_ai import hvac_parsing, reference_resolution
from drawing_ai.agents import parent_agent
from drawing_ai.schemas import DimensionReading, Phase3Result, QAItem, SpecItem, SpecResult, SpecRoom


# --- 誤り1: 洗面化粧台(質疑No.6参照の解決漏れ) --------------------------


def test_find_reference_matches_real_kdx_phrasing():
    assert reference_resolution.find_reference("質疑No.6参照") == 6


def test_resolve_references_follows_one_hop():
    items = [
        {"item_no": 6, "answer": "撤去実績はありません。見積仕様としては撤去としてください。"},
        {"item_no": 22, "answer": "質疑No.6参照"},
    ]
    resolved = reference_resolution.resolve_references(items)
    q22 = next(i for i in resolved if i["item_no"] == 22)
    assert "撤去" in q22["answer"]
    assert q22["resolved_from_reference"] == 6


def test_resolve_references_flags_unresolvable_chain():
    items = [
        {"item_no": 1, "answer": "質疑No.2参照"},
        {"item_no": 2, "answer": "質疑No.3参照"},
    ]
    resolved = reference_resolution.resolve_references(items)
    q1 = next(i for i in resolved if i["item_no"] == 1)
    assert q1.get("unresolved_chained_reference") == 2


def test_aggregate_spec_resolves_qa_items_across_tiles():
    # Item 6 was read from one tile, item 22 (which references it) from
    # another -- resolution must happen after merging, not per-tile.
    qa_items = [
        QAItem(item_no=6, category="建築", question="洗面台横のボックスは撤去可能か",
               answer="撤去実績はありません。見積仕様としては撤去としてください。",
               resolved_answer="撤去実績はありません。見積仕様としては撤去としてください。"),
        QAItem(item_no=22, category="建築", question="洗面台脇の天板は既存利用でよいか",
               answer="質疑No.6参照", resolved_answer="質疑No.6参照"),
    ]
    result = parent_agent.aggregate_spec([], all_qa_items=qa_items)
    q22 = next(i for i in result.qa_items if i.item_no == 22)
    assert "撤去" in q22.resolved_answer
    assert q22.resolved_from_item_no == 6


# --- 見落とし・区分ズレ: 空調系統の台数解釈 -------------------------------


def test_parse_multi_split_notation_matches_real_kdx_phrasing():
    systems = hvac_parsing.parse_multi_split_notation("1対1×2組、1対2×1組")
    assert hvac_parsing.total_units(systems) == (3, 4)  # ブラインド推論時の(誤った)解釈


def test_parse_daikin_branch_count_matches_real_model_numbers():
    assert hvac_parsing.parse_daikin_branch_count("3M685AV") == 3
    assert hvac_parsing.parse_daikin_branch_count("2M535AV") == 2
    assert hvac_parsing.parse_daikin_branch_count("C36ZCV") is None  # 室内機側の型番は非対象


def test_check_prose_vs_equipment_prefers_equipment_and_flags_conflict():
    outdoor, indoor_capacity, notes = hvac_parsing.check_prose_vs_equipment(
        "1対1×2組、1対2×1組", ["3M685AV", "2M535AV"]
    )
    # 実際の見積(原価内訳書)は室外機2台 -- 機器型番ベースの値が正解に近い
    assert outdoor == 2
    assert indoor_capacity == 5  # 3+2分岐の「最大」であり確定台数ではない
    assert any("食い違います" in n for n in notes)
    assert any("最大分岐数" in n for n in notes)  # 確定台数ではない旨のcaveatを必ず含む


def test_check_prose_vs_equipment_no_conflict_note_without_prose():
    outdoor, indoor_capacity, notes = hvac_parsing.check_prose_vs_equipment("", ["3M685AV"])
    assert outdoor == 1
    assert indoor_capacity == 3
    assert not any("食い違います" in n for n in notes)  # 比較対象の記述が無ければ矛盾フラグは立てない


# --- スライディングウォール寸法の不一致(COAI-01の保留事項の解消) --------


def _dim(raw_text: str, verified: bool) -> DimensionReading:
    return DimensionReading(tile_id="t1", raw_text=raw_text, confidence=0.8, verified_by_vector=verified)


def test_check_spec_dimensions_flags_mismatch_with_real_kdx_numbers():
    # 実際の食い違い: 質疑書はW3640×H2400、図面のベクター確定寸法は3548と2035
    phase3 = Phase3Result(dimensions=[_dim("3548", True), _dim("2035", True), _dim("8356", True)])
    spec = SpecResult(
        qa_items=[
            QAItem(item_no=23, question="スライディングウォールの仕様",
                   answer="PANASONIC製、W3640×H2400", resolved_answer="PANASONIC製、W3640×H2400")
        ]
    )
    flags = parent_agent.check_spec_dimensions_against_drawing(spec, phase3)
    assert len(flags) == 1
    assert "W3640×H2400" in flags[0]


def test_check_spec_dimensions_no_flag_when_matching():
    phase3 = Phase3Result(dimensions=[_dim("3548", True), _dim("2035", True)])
    spec = SpecResult(
        qa_items=[QAItem(item_no=1, answer="W3548×H2035", resolved_answer="W3548×H2035")]
    )
    assert parent_agent.check_spec_dimensions_against_drawing(spec, phase3) == []


def test_check_spec_dimensions_no_flag_without_vector_ground_truth():
    # ベクター確定寸法が無いシートでは(比較材料が無いため)何も断定しない。
    phase3 = Phase3Result(dimensions=[_dim("3548", False)])
    spec = SpecResult(
        qa_items=[QAItem(item_no=1, answer="W3640×H2400", resolved_answer="W3640×H2400")]
    )
    assert parent_agent.check_spec_dimensions_against_drawing(spec, phase3) == []


def test_check_spec_dimensions_checks_room_spec_notes_too():
    phase3 = Phase3Result(dimensions=[_dim("650", True)])
    room = SpecRoom(room_name="洗面")
    room.specs.append(SpecItem(finish="タイル", notes="開口 W900×H2000 を確保"))
    spec = SpecResult(rooms=[room])
    flags = parent_agent.check_spec_dimensions_against_drawing(spec, phase3)
    assert len(flags) == 1
    assert "W900×H2000" in flags[0]
