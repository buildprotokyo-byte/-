"""Tests for the fixes made after the KDX802 blind-test verification report.

Each test is anchored to a specific, named root cause from that report
(see VERIFICATION_REPORT_KDX802.md) rather than a generic case, and where
possible uses the real numbers found in that project so these tests would
have actually caught the original mistake.
"""
from __future__ import annotations

from drawing_ai import grounding, hvac_parsing, reference_resolution
from drawing_ai.agents import parent_agent
from drawing_ai.schemas import (
    DimensionReading,
    ElementReading,
    Phase3Result,
    QAItem,
    SpecItem,
    SpecResult,
    SpecRoom,
    Tile,
)


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


# --- 段階1(文字認識)の欠陥: OCR文字レイヤーをCADネイティブと誤って信頼 -----


def _gt_tile(tier: str, text: str = "") -> Tile:
    return Tile(
        tile_id="t1", sheet_id="s1", sheet_index=0, row=0, col=0,
        x0=0, y0=0, x1=100, y1=100, image_path="unused.png",
        ground_truth_text=text, has_vector_ground_truth=True,
        ground_truth_trust_tier=tier,
    )


def test_is_cad_native_true_only_for_cad_native_tier():
    assert grounding.is_cad_native(_gt_tile("cad_native")) is True
    assert grounding.is_cad_native(_gt_tile("ocr_layer_over_scan")) is False


def test_numeric_adjustment_never_verifies_ocr_layer_tier():
    # KDX802の実際の食い違い方: OCR文字レイヤーには数字自体は正しく含まれて
    # いても、CADネイティブと同じ信頼(verified_by_vector=True)を与えては
    # ならない。
    tile = _gt_tile("ocr_layer_over_scan", text="3548 2035")
    delta, verified = grounding.numeric_adjustment("3548", tile)
    assert verified is False
    assert delta == 0.1  # OCR相当の弱い加点のみ


def test_numeric_adjustment_verifies_cad_native_tier():
    tile = _gt_tile("cad_native", text="3548 2035")
    delta, verified = grounding.numeric_adjustment("3548", tile)
    assert verified is True
    assert delta == 0.3


def test_text_adjustment_lower_bonus_for_ocr_layer_tier():
    ocr_tile = _gt_tile("ocr_layer_over_scan", text="洋室-B")
    cad_tile = _gt_tile("cad_native", text="洋室-B")
    ocr_delta, ocr_verified = grounding.text_adjustment("洋室-B", ocr_tile)
    cad_delta, cad_verified = grounding.text_adjustment("洋室-B", cad_tile)
    assert ocr_verified is False and ocr_delta == 0.1
    assert cad_verified is True and cad_delta == 0.25


# --- 段階3/5: 同一ラベルの離れた要素を位置無視で1個に潰していた数え落とし ---


def _tile_at(tile_id: str, row: int, col: int, sheet_id: str = "s1") -> Tile:
    return Tile(
        tile_id=tile_id, sheet_id=sheet_id, sheet_index=0, row=row, col=col,
        x0=0, y0=0, x1=100, y1=100, image_path="unused.png",
    )


def _room_element(tile_id: str, label: str = "ダウンライト", confidence: float = 0.7) -> ElementReading:
    return ElementReading(
        element_id=f"e-{tile_id}", element_type="fixture", label_ja=label,
        sheet_id="s1", tile_ids=[tile_id], confidence=confidence,
    )


def test_dedupe_elements_merges_only_adjacent_tiles():
    # 3箇所のダウンライト(矢野様邸・KDX802双方の実例と同種)が、互いに離れた
    # タイル(r0c0, r5c5, r9c9)でそれぞれ検知されたケース。位置を見なければ
    # 「ダウンライト」という同一ラベルだけで1個に潰れてしまう。
    tiles_by_id = {
        "t1": _tile_at("t1", 0, 0),
        "t2": _tile_at("t2", 5, 5),
        "t3": _tile_at("t3", 9, 9),
    }
    elements = [_room_element("t1"), _room_element("t2"), _room_element("t3")]
    out = parent_agent._dedupe_elements(elements, tiles_by_id)
    assert len(out) == 3  # 3箇所とも別個体として残る(数え落としが直った)


def test_dedupe_elements_merges_same_instance_seen_in_overlapping_tiles():
    # タイル重複(r0c0とr0c1)で同じ現物を2回読んだケースは、従来通り1個に
    # 統合されなければならない(過剰カウントを防ぐ)。
    tiles_by_id = {"t1": _tile_at("t1", 0, 0), "t2": _tile_at("t2", 0, 1)}
    elements = [_room_element("t1", confidence=0.6), _room_element("t2", confidence=0.8)]
    out = parent_agent._dedupe_elements(elements, tiles_by_id)
    assert len(out) == 1
    assert out[0].confidence == 0.8
    assert set(out[0].tile_ids) == {"t1", "t2"}


def test_dedupe_elements_without_position_info_never_merges():
    # tiles_by_idが無い(位置が分からない)場合は、数え落としより数え過ぎの
    # 方が安全(人間が見て重複に気付ける)という方針を取る。
    elements = [_room_element("t1"), _room_element("t2")]
    out = parent_agent._dedupe_elements(elements, tiles_by_id=None)
    assert len(out) == 2


# --- 敷地(Phase1)の精度: 縮尺とスロープ勾配の混同を防ぐ -------------------
# ユーザーの指示により、ハードルが低いと考えられる「敷地」の文字・数字読み
# 取り精度を優先して見直した際に見つかったバグ。敷地図にはスロープ勾配
# (例: 1/12)のような「縮尺と同じ見た目のN/M表記」が、縮尺そのもの
# (例: 1/50)とは別に載っていることが多い。


from drawing_ai import vector_extractor as ve


def test_find_scale_picks_nearest_to_label_even_with_slope_present():
    label = ve.GroundTruthWord(text="縮尺", x0=500, y0=500, x1=520, y1=510)
    real_scale = ve.GroundTruthWord(text="1/50", x0=525, y0=500, x1=545, y1=510)
    slope = ve.GroundTruthWord(text="1/12", x0=0, y0=0, x1=20, y1=10)
    text, denom = ve._find_scale([slope, label, real_scale])
    assert text == "1/50"
    assert denom == 50


def test_find_scale_refuses_to_guess_when_ambiguous_without_label():
    slope = ve.GroundTruthWord(text="1/12", x0=0, y0=0, x1=20, y1=10)
    real_scale = ve.GroundTruthWord(text="1/50", x0=500, y0=500, x1=520, y1=510)
    # 「縮尺」ラベルが無いため、どちらが本当の縮尺か判断できない -> 推測しない
    text, denom = ve._find_scale([slope, real_scale])
    assert text is None
    assert denom is None


def test_find_scale_still_works_with_single_unambiguous_token():
    only_scale = ve.GroundTruthWord(text="1/50", x0=500, y0=500, x1=520, y1=510)
    text, denom = ve._find_scale([only_scale])
    assert text == "1/50"
    assert denom == 50


# --- 識字(文字認識)精度: 断片化した自由記述(住所等)の取り違え防止 -------


from drawing_ai.schemas import SiteFact


def test_aggregate_phase1_prefers_complete_address_over_truncated_fragment():
    fragment = SiteFact(key="address", label_ja="所在地", value="石川県", confidence=0.75)
    complete = SiteFact(key="address", label_ja="所在地", value="石川県金沢市XX町1-2-3", confidence=0.65)
    result = parent_agent.aggregate_phase1([fragment, complete])
    assert len(result.facts) == 1
    assert result.facts[0].value == "石川県金沢市XX町1-2-3"


def test_aggregate_phase1_prefers_complete_address_regardless_of_order():
    fragment = SiteFact(key="address", label_ja="所在地", value="石川県", confidence=0.9)
    complete = SiteFact(key="address", label_ja="所在地", value="石川県金沢市XX町1-2-3", confidence=0.5)
    result = parent_agent.aggregate_phase1([complete, fragment])
    assert result.facts[0].value == "石川県金沢市XX町1-2-3"


# --- 識字(文字認識)精度: OCRエンジンのフォールバックが無言だった問題 -------


def test_get_ocr_engine_warns_and_falls_back_when_dependency_missing(monkeypatch, caplog):
    from drawing_ai import ocr_engine as oe

    monkeypatch.setattr(oe.settings, "ocr_engine", "paddleocr")
    oe.get_ocr_engine.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="drawing_ai.ocr_engine"):
            engine = oe.get_ocr_engine()
        assert isinstance(engine, oe.NullOcrEngine)
        assert any("falling back to no OCR grounding" in r.message for r in caplog.records)
    finally:
        oe.get_ocr_engine.cache_clear()


def test_get_ocr_engine_warns_on_unknown_engine_name(monkeypatch, caplog):
    from drawing_ai import ocr_engine as oe

    monkeypatch.setattr(oe.settings, "ocr_engine", "not_a_real_engine")
    oe.get_ocr_engine.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="drawing_ai.ocr_engine"):
            engine = oe.get_ocr_engine()
        assert isinstance(engine, oe.NullOcrEngine)
        assert any("unknown DRAWING_AI_OCR_ENGINE" in r.message for r in caplog.records)
    finally:
        oe.get_ocr_engine.cache_clear()
