"""要素ごとに専門知識を選んで読む段(`knowledge/expertise/reader.py`)の試験。

読み手(AI)は偽物を渡す。図面も答えも合成である。
"""

from __future__ import annotations

import copy
import json

import pytest

from knowledge.expertise import reader as er


@pytest.fixture(scope="module")
def catalog():
    return er.load_catalog()


def _payload():
    return json.loads(er.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))


def _answer(**overrides):
    body = {
        "page_structure": {"drawing_kind": "平面図", "scale": "1:50", "subject": "1階 住戸A",
                           "intent": "改修後の間取り", "title_block_evidence": "右下の表題欄 図面名"},
        "status": er.STATUS_READ,
        "answer": {
            "basis": "芯々",
            "rectangles": [{"width_mm": 3640, "depth_mm": 2730, "sign": 1,
                            "width_from": "下側の寸法列 X1-X2", "depth_from": "右側の寸法列 Y1-Y2"}],
            "area_m2": 9.94,
        },
        "used_knowledge": ["床面積の算定_中心線", "建築製図通則"],
        "drawing_evidence": ["平面図 下側の寸法列 3640"],
        "reason": "室を囲む通り芯のあいだの寸法",
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def _fake(text):
    calls = []

    def reader(premise, request):
        calls.append((premise, request))
        return text

    reader.reader_id = "偽物"
    reader.calls = calls
    return reader


# -- 目録 --------------------------------------------------------------------


def test_every_element_and_drawing_kind_has_knowledge(catalog):
    assert catalog.uncovered_elements() == ()
    assert catalog.uncovered_drawing_kinds() == ()


def test_foundation_is_always_included(catalog):
    """**土台は要素や図の種類で外れない**(おーちゃんの指示 2026-09-24 15:02)。"""
    for element in catalog.elements:
        for kind in (None, *[k.kind_id for k in catalog.drawing_kinds]):
            sel = er.select_knowledge(catalog, element.element_id, kind)
            assert tuple(k.knowledge_id for k in sel.foundation) == catalog.foundation
            ids = sel.knowledge_ids
            assert len(ids) == len(set(ids)), "1 件の知識が 2 つの層に入っている"


def test_foundation_covers_drafting_law_and_licences(catalog):
    base = set(catalog.foundation)
    assert {"図面の構造_用紙と様式", "線の太さと種類", "建築基準法の骨格",
            "一級建築士の知識", "施工管理技士の知識"} <= base


def test_drawing_kind_adds_its_own_layer(catalog):
    plan = er.select_knowledge(catalog, "寸法", "平面図")
    bare = er.select_knowledge(catalog, "寸法")
    assert bare.kind_knowledge == ()
    assert "基準線_通り芯" in [k.knowledge_id for k in plan.kind_knowledge]


def test_unknown_drawing_kind_is_refused(catalog):
    with pytest.raises(er.ExpertiseError, match="知らない図の種類"):
        er.select_knowledge(catalog, "寸法", "屋根伏図もどき")


def test_room_area_selects_law_drawing_and_estimating(catalog):
    ids = er.select_knowledge(catalog, er.ELEMENT_ROOM_AREA).knowledge_ids
    assert "床面積の算定_中心線" in ids
    assert "建築製図通則" in ids
    assert "公共建築数量積算基準" in ids
    assert "基準線_通り芯" in ids
    # 電気の記号の知識は面積には選ばれない
    assert "配線用図記号" not in ids


def test_no_point_claims_checked_without_reading_source(catalog):
    # 原文(JIS・法令)を直接読んで照らした要点はまだ 1 件も無い
    assert er.POINT_CHECKED not in {k.point_status for k in catalog.knowledge}
    for k in catalog.knowledge:
        if k.point_status == er.POINT_FROM_OWNER:
            assert k.taken_from.startswith("docs/drafting_rules_reference.md")


def test_owner_point_needs_where_it_was_taken_from():
    payload = _payload()
    entry = next(k for k in payload["knowledge"] if k["point_status"] == er.POINT_FROM_OWNER)
    del entry["taken_from"]
    with pytest.raises(er.ExpertiseError, match="taken_from"):
        er.parse_catalog(payload)


def test_foundation_pointing_to_unknown_knowledge_is_refused():
    payload = _payload()
    payload["foundation"].append("無い知識")
    with pytest.raises(er.ExpertiseError, match="土台"):
        er.parse_catalog(payload)


def test_unknown_column_is_refused():
    payload = _payload()
    payload["knowledge"][0]["本文"] = "写した本文"
    with pytest.raises(er.ExpertiseError, match="知らない列"):
        er.parse_catalog(payload)


def test_missing_source_is_refused():
    payload = _payload()
    del payload["knowledge"][0]["source"]
    with pytest.raises(er.ExpertiseError, match="出どころ"):
        er.parse_catalog(payload)


def test_checked_point_needs_direct_reading():
    payload = _payload()
    payload["knowledge"][0]["point_status"] = er.POINT_CHECKED
    with pytest.raises(er.ExpertiseError, match="照合済み"):
        er.parse_catalog(payload)


def test_knowledge_pointing_to_unknown_element_is_refused():
    payload = _payload()
    payload["knowledge"][0]["elements"] = ["存在しない要素"]
    with pytest.raises(er.ExpertiseError, match="目録に無い要素"):
        er.parse_catalog(payload)


def test_unknown_element_is_refused(catalog):
    with pytest.raises(er.ExpertiseError, match="知らない要素"):
        er.select_knowledge(catalog, "屋根の勾配")


# -- 前提の文 ----------------------------------------------------------------


def test_premise_names_roles_sources_and_drawing_priority(catalog):
    premise = er.build_premise(er.select_knowledge(catalog, er.ELEMENT_ROOM_AREA, "平面図"), catalog)
    assert "一級建築士" in premise and "建築積算士" in premise
    assert "建築基準法施行令" in premise
    assert "図面を採って" in premise
    assert "質疑にあたる" in premise


def test_premise_keeps_the_reading_order_and_layers(catalog):
    """**順番は 構造 → 図の種類 → 表現の根拠。**土台は要素の知識より先に出る。"""
    premise = er.build_premise(er.select_knowledge(catalog, er.ELEMENT_ROOM_AREA, "平面図"), catalog)
    positions = [premise.index(f"{i}. {s.step}:") for i, s in enumerate(catalog.reading_order, start=1)]
    assert positions == sorted(positions)
    assert [s.step for s in catalog.reading_order] == ["図面の構造", "図の種類の書き方", "表現の根拠から推論"]
    assert premise.index("【土台】") < premise.index("【図の種類の書き方】") < premise.index("【要素の専門知識】")


# -- 読ませる ----------------------------------------------------------------


def test_without_reader_nothing_is_invented(catalog):
    request = er.room_area_request("居室A", materials=["寸法 3640 2730"])
    reading = er.read_element(catalog, request, None, unavailable_reason="鍵が無い")
    assert reading.status == er.STATUS_NOT_CONNECTED
    assert reading.answer == {}
    assert reading.reason == "鍵が無い"


def test_request_without_material_is_refused():
    with pytest.raises(er.ExpertiseError, match="資料"):
        er.room_area_request("居室A")


def test_reading_is_marked_and_never_confirmed(catalog):
    reader = _fake(_answer())
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.origin == er.ORIGIN_KNOWLEDGE
    assert reading.confirmed is False
    assert reading.answer["area_m2_recomputed"] == "9.94"
    assert reading.notes == ()
    premise, _ = reader.calls[0]
    assert "床面積の算定_中心線" in premise
    assert reading.page_structure["drawing_kind"] == "平面図"
    assert reading.used_by_layer[er.LAYER_FOUNDATION] == 1  # 建築製図通則
    assert reading.used_by_layer[er.LAYER_ELEMENT] == 1  # 床面積の算定_中心線


def test_skipping_page_structure_is_recorded(catalog):
    reader = _fake(_answer(page_structure={}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert any("1 段目" in n for n in reading.notes)


def test_kind_disagreement_is_recorded(catalog):
    reader = _fake(_answer(page_structure={"drawing_kind": "天井伏図", "scale": "1:50", "subject": "1階", "intent": "天井"}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert any("渡した図の種類" in n for n in reading.notes)


def test_unreadable_must_say_which_of_three(catalog):
    reader = _fake(_answer(status=er.STATUS_UNREADABLE, unreadable={"kind": "よく分からない"}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.answer == {}
    assert any("3 つ" in n for n in reading.notes)
    reader = _fake(_answer(status=er.STATUS_UNREADABLE, unreadable={
        "kind": er.UNREADABLE_NO_RULE, "tried_knowledge": ["基準線_通り芯"],
        "expected_by_rule": "外周に円と記号", "actual": "円が無い"}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.unreadable["kind"] == er.UNREADABLE_NO_RULE
    assert not any("3 つ" in n for n in reading.notes)


def test_area_is_recomputed_not_trusted(catalog):
    reader = _fake(_answer(answer={"basis": "芯々", "rectangles": [{"width_mm": 3640, "depth_mm": 2730, "sign": 1}], "area_m2": 12.0}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert any("合わない" in n for n in reading.notes)


def test_l_shaped_room_subtracts(catalog):
    rects = [{"width_mm": 4000, "depth_mm": 3000, "sign": 1},
             {"width_mm": 1000, "depth_mm": 1000, "sign": -1}]
    reader = _fake(_answer(answer={"basis": "芯々", "rectangles": rects, "area_m2": 11.0}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.answer["area_m2_recomputed"] == "11.00"
    assert reading.notes == ()


def test_inner_basis_is_flagged_against_the_decision(catalog):
    reader = _fake(_answer(answer={"basis": "内法", "rectangles": [{"width_mm": 3000, "depth_mm": 2000, "sign": 1}], "area_m2": 6}))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert any("会社の決め" in n for n in reading.notes)


def test_invented_knowledge_names_are_kept_apart(catalog):
    reader = _fake(_answer(used_knowledge=["建築製図通則", "作った基準"]))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.used_knowledge == ("建築製図通則",)
    assert reading.unknown_knowledge == ("作った基準",)


def test_query_answer_carries_no_value(catalog):
    reader = _fake(_answer(status=er.STATUS_QUERY, reason="寸法が 2 通りに読める"))
    reading = er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)
    assert reading.status == er.STATUS_QUERY
    assert reading.answer == {}


def test_bad_status_is_refused(catalog):
    reader = _fake(_answer(status="たぶん"))
    with pytest.raises(er.ExpertiseError, match="status"):
        er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)


# -- 図面との突き合わせ ------------------------------------------------------


def _area_reading(catalog):
    reader = _fake(_answer())
    return er.read_element(catalog, er.room_area_request("居室A", materials=["x"]), reader)


def test_drawing_wins_when_it_disagrees(catalog):
    check = er.check_against_drawing(_area_reading(catalog), er.DrawingFact(er.FACT_AREA, "10.20", "求積表"))
    assert check.result == er.CHECK_CONFLICT
    assert check.adopted_value == "10.20"


def test_agreement_with_printed_area(catalog):
    check = er.check_against_drawing(_area_reading(catalog), er.DrawingFact(er.FACT_AREA, 9.94, "求積表"))
    assert check.result == er.CHECK_AGREED


def test_no_drawing_fact_keeps_knowledge_only_answer(catalog):
    check = er.check_against_drawing(_area_reading(catalog), None)
    assert check.result == er.CHECK_NOTHING_TO_CHECK
    assert check.adopted_value == "9.94"


def test_symbol_name_against_legend(catalog):
    reader = _fake(json.dumps({"page_structure": {"drawing_kind": "空調衛生設備図", "scale": "1:50", "subject": "1階", "intent": "換気"},
                               "status": er.STATUS_READ, "answer": {"name": "換気扇"},
                               "used_knowledge": ["空調衛生の図示記号"], "drawing_evidence": ["平面図"], "reason": ""},
                              ensure_ascii=False))
    request = er.ElementReadingRequest(element_id="設備の記号", question="この記号は何か", materials=["記号"])
    reading = er.read_element(catalog, request, reader)
    check = er.check_against_drawing(reading, er.DrawingFact(er.FACT_NAME, "天井扇", "凡例"))
    assert check.result == er.CHECK_CONFLICT
    assert check.adopted_value == "天井扇"


# -- 読み手をつなぐ ----------------------------------------------------------


def test_reader_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    reader, why = er.connect_reader()
    assert reader is None
    assert "鍵" in why


def test_anthropic_reader_sends_premise_as_system():
    sent = {}

    class _Block:
        type = "text"
        text = _answer()

    class _Response:
        stop_reason = "end_turn"
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return _Response()

    class _Client:
        messages = _Messages()

    reader = er.AnthropicReader(model="claude-opus-5", client=_Client())
    request = er.room_area_request("居室A", images=[("image/png", b"\x89PNG")])
    out = reader("前提の文", request)
    assert sent["system"] == "前提の文"
    assert sent["messages"][0]["content"][0]["type"] == "image"
    assert json.loads(out)["status"] == er.STATUS_READ


def test_cli_lists_elements(capsys):
    assert er.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "土台(いつも入る)" in out and "要素 室の面積:" in out
