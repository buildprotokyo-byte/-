"""K-73 作業 4: 図面に室の寸法が無い行の「分からない」と、室ごとの 1 問(`docs/k73_room_unknown_criteria.md`)。

**合成のデータだけ**(室は「室A」などの作った名前、図面は試験で作る PDF)。確かめること:
- C の行に「分からない(図面に室の寸法が無い)」と、4 つの種類の全部の探したページが付き、数量は None のまま。
- 数字の入力欄は、4 つの種類の全部で「無かった」室だけ。1 つでも候補があった・見られなかった室には出ない。
- 概算では聞かない。通常 5・精密 10 まで。1 室 1 問。メーター 3 つ。原価表が無ければ金額の割合は未取得(0 にしない)。
- 答えを戻すと、要る辺が全部そろった行だけ数量が出る。人の入力の印・確度「中」・自動確定にしない。
- 旗オフで出力が変わらない。旗オンでも自動確定 0。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from draft import room_unknown as ru
from draft.run import run
from tests.test_draft_flags import FlagClient, _run, machine_output  # noqa: F401  (fixture)

ALL_NONE = {k: {"ページ": [1], "結果": ru.FOUND_NONE} for k in ru.SEARCH_KINDS}


def _room(no: int, yoko: str, tate: str, *, ceiling: bool = True, nr: bool = False, values=None) -> dict:
    return {"番号": no, "辺": {"横": yoko, "縦": tate}, "辺の id": {"横": "P8-D1" if yoko == ru.SIDE_READ else None,
                                                             "縦": "P8-D2" if tate == ru.SIDE_READ else None},
            "分け先": ru.room_category({"横": yoko, "縦": tate}, not_rectangle=nr), "天井高": ceiling,
            "天井高_mm": 2400 if ceiling else None, "長方形でない": nr, "_名前の語": [], "_鍵": set(), "_値": [],
            "_辺の値": values or {"横": [], "縦": []}}


def _item(i: int, place: str, part: str, unit: str, qty=None) -> dict:
    return {"id": f"p1-{i:03d}", "場所": place, "部位": part, "単位": unit, "数量": qty, "工事": f"工事{i}",
            "品番": f"X-{i}", "ページ": 1}


@pytest.fixture()
def rooms() -> dict:
    from draft.stages import room_key

    return {
        room_key("室A"): _room(0, ru.SIDE_ABSENT, ru.SIDE_ABSENT),
        room_key("室B"): _room(1, ru.SIDE_READ, ru.SIDE_ABSENT, values={"横": [1800], "縦": []}),
        room_key("室C"): _room(2, ru.SIDE_ABSENT, ru.SIDE_ABSENT, ceiling=False),
        room_key("室D"): _room(3, ru.SIDE_READ, ru.SIDE_READ, values={"横": [1000], "縦": [2000]}),
        room_key("室E"): _room(4, ru.SIDE_RULER, ru.SIDE_ABSENT, values={"横": [900], "縦": []}),
    }


@pytest.fixture()
def items() -> list:
    return [
        _item(1, "室A", "床", "m2"), _item(2, "室A", "幅木", "m"), _item(3, "室A", "壁", "㎡"),
        _item(4, "室B", "天井", "m2"), _item(5, "室C", "壁", "m2"), _item(6, "室C", "床", "m2"),
        _item(7, "室D", "床", "m2"), _item(8, "室A 室D", "床", "m2"), _item(9, "室E", "床", "m2"),
        _item(10, "室A", "電気", "m"), _item(11, "どこか", "床", "m2"), _item(12, "室A", "床", "m2", qty=5.0),
        _item(13, "室A", "床", "個"),
    ]


def test_c_rows_follow_k72_order(rooms, items):
    rows = ru.c_rows(items, rooms)
    ids = [r["item"]["id"] for r in rows]
    # 室D だけの行は A、場所が結べない行は D1、電気は D2、数量のある行・面積長さでない行は対象外
    assert ids == ["p1-001", "p1-002", "p1-003", "p1-004", "p1-005", "p1-006", "p1-008", "p1-009"]
    by = {r["item"]["id"]: r for r in rows}
    assert by["p1-005"]["marks"].get("天井高が無い")
    assert set(by["p1-008"]["keys"]) and len(by["p1-008"]["keys"]) == 2


def test_marked_rows_say_dont_know_with_all_four_kinds_and_keep_none(rooms, items):
    rows = ru.c_rows(items, rooms)
    searches = {0: ALL_NONE, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}
    marked = ru.mark_rows(rows, rooms, searches)
    assert len(marked) == len(rows)
    for m in marked:
        assert m["数量"] is None
        assert m["分からない"].startswith(ru.REASON)
        assert any(r["図面に無い辺"] for r in m["室"])
        for r in m["室"]:
            if r["図面に無い辺"]:
                assert set(r["探したページ"]) == set(ru.SEARCH_KINDS)
            else:
                assert r["注"]
    wall_c = next(m for m in marked if m["項目"] == "p1-005")
    assert ru.NO_CEILING in wall_c["分からない"]
    # 元の項目の数量は書き換えない
    assert all(it["数量"] is None for it in items if it["id"] in {m["項目"] for m in marked})


def test_missing_kind_pages_are_written_not_blank():
    s = ru.searched_summary({"平面図": {"ページ": [2], "結果": ru.FOUND_NONE}})
    assert s["展開図"]["結果"] == ru.NOT_SEARCHED and s["展開図"]["注"]
    assert all(s[k]["結果"] for k in ru.SEARCH_KINDS)


@pytest.mark.parametrize("bad_kind", ru.SEARCH_KINDS)
@pytest.mark.parametrize("bad", [ru.FOUND_CANDIDATE, ru.NOT_SEARCHED])
def test_numeric_input_only_when_all_four_kinds_found_nothing(rooms, items, bad_kind, bad):
    search = {k: dict(v) for k, v in ALL_NONE.items()}
    search[bad_kind]["結果"] = bad
    assert ru.numeric_allowed(ALL_NONE)
    assert not ru.numeric_allowed(search)
    rows = ru.c_rows(items, rooms)
    qs = ru.build_questions(rows, rooms, {0: search, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}, len(items))
    q0 = next(q for q in qs if q["室"] == 0)
    assert q0["数字の入力"] == [] and ru.OPT_NUMBER not in q0["選択肢"]
    assert q0["メーター"]["この答えで確定する行数"] == 0
    q1 = next(q for q in qs if q["室"] == 1)
    assert q1["数字の入力"] == ["縦_mm"]  # 図面に無い辺だけ
    assert q1["選択肢"] == [ru.OPT_NUMBER, ru.OPT_DONT_KNOW, ru.OPT_SITE]
    assert q1["数字の入力の印"] == ru.HUMAN


def test_one_question_per_room_with_three_meters_and_no_amount_zero(rooms, items):
    rows = ru.c_rows(items, rooms)
    searches = {0: ALL_NONE, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}
    qs = ru.build_questions(rows, rooms, searches, len(items))
    assert sorted(q["室"] for q in qs) == [0, 1, 2, 4]
    for q in qs:
        m = q["メーター"]
        assert {"この答えで確定する行数", "金額の割合", "回答時間の見積(秒)"} <= set(m)
        assert m["金額の割合"] == "未取得(原価表 未取得)"
        assert q["推奨"] is None
    by = {q["室"]: q["メーター"] for q in qs}
    assert by[0]["この答えで確定する行数"] == 3  # 床・幅木・壁(室A だけの行)
    assert by[0]["ほかの室の答えも要る行数"] == 1  # 室A 室D の床
    assert by[1]["この答えで確定する行数"] == 1
    assert by[2]["この答えで確定する行数"] == 1  # 床は出る、壁は天井高が無いので出ない
    assert by[4]["この答えで確定する行数"] == 0  # もう 1 辺が目盛りの直し待ち
    levels = ru.by_level(qs)
    assert levels["概算"] == []
    assert len(levels["通常"]) <= 5 and len(levels["精密"]) <= 10
    assert [q["室"] for q in levels["精密"]][0] == 0


def test_answers_confirm_only_complete_rows_marked_human(rooms, items):
    rows = ru.c_rows(items, rooms)
    searches = {0: ALL_NONE, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}
    qs = ru.build_questions(rows, rooms, searches, len(items))
    answers = {
        "室の寸法:0": {"選択肢": ru.OPT_NUMBER, "横_mm": 2000, "縦_mm": 3000},
        "室の寸法:1": {"選択肢": ru.OPT_NUMBER, "縦_mm": 2500, "横_mm": 99},  # 図面にある辺の数字は受けない
        "室の寸法:2": ru.OPT_SITE,
        "室の寸法:4": {"選択肢": ru.OPT_NUMBER, "縦_mm": "3000"},  # 文字は受けない
    }
    back = ru.apply_answers(rows, rooms, qs, answers)
    got = {c["項目"]: c for c in back["確定した行"]}
    assert set(got) == {"p1-001", "p1-002", "p1-003", "p1-008"}
    assert got["p1-001"]["数量"] == 6.0
    assert got["p1-002"]["数量"] == 10.0
    assert got["p1-003"]["数量"] == pytest.approx(24.0)
    assert got["p1-008"]["数量"] == 8.0  # 室A 6 + 室D 2(図面の辺)
    for c in got.values():
        assert c["印"] == ru.HUMAN and c["自動確定"] is False and c["確度"] == "中" and c["状態"] == "推論"
        assert c["根拠"]["人の入力"]
    assert {r["鍵"] for r in back["受け取れなかった答え"]} == {"室の寸法:1", "室の寸法:4"}
    assert back["金額の割合"] == "未取得(原価表 未取得)"
    # 元の項目は書き換えない
    assert all(it["数量"] is None for it in items if it["id"] in got)


def test_numbers_for_rooms_not_allowed_are_rejected(rooms, items):
    rows = ru.c_rows(items, rooms)
    bad = {k: dict(v) for k, v in ALL_NONE.items()}
    bad["展開図"]["結果"] = ru.FOUND_CANDIDATE
    qs = ru.build_questions(rows, rooms, {0: bad, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}, len(items))
    back = ru.apply_answers(rows, rooms, qs, {"室の寸法:0": {"選択肢": ru.OPT_NUMBER, "横_mm": 2000, "縦_mm": 3000}})
    assert back["確定した行数"] == 0
    assert back["受け取れなかった答え"][0]["鍵"] == "室の寸法:0"


def test_amount_share_with_cost_table(rooms, items):
    rows = ru.c_rows(items, rooms)
    qs = ru.build_questions(rows, rooms, {0: ALL_NONE}, len(items), cost_table={"行": [
        {"工事": "工事1", "品番": "X-1", "単位": "m2", "単価": 100.0, "数量": None, "科目": ""}]})
    q0 = next(q for q in qs if q["室"] == 0)
    assert q0["メーター"]["金額の割合"]["単価が当たった行"] == 1
    table = {"行": [{"工事": "工事1", "品番": "X-1", "単位": "m2", "単価": 100.0, "数量": None, "科目": ""},
                   {"工事": "他", "品番": "Y", "単位": "式", "単価": 600.0, "数量": None, "科目": ""}]}
    back = ru.apply_answers(rows, rooms, qs, {"室の寸法:0": {"選択肢": ru.OPT_NUMBER, "横_mm": 2000, "縦_mm": 3000}},
                            cost_table=table, assembly_rows=[{"工事項目": "他", "摘要": "Y", "単位": "式", "数量": 1}])
    assert back["金額の割合"] == pytest.approx(600 / 1200)


def test_structural_counts_do_not_invent_values(rooms, items):
    rows = ru.c_rows(items, rooms)
    qs = ru.build_questions(rows, rooms, {0: ALL_NONE, 1: ALL_NONE, 2: ALL_NONE, 4: ALL_NONE}, len(items))
    s = ru.structural(rows, rooms, qs, len(items))
    assert s["数字の入力を認めた室が全部答えたとき 確定する行数"] == 6  # 室A 3 + 室B 1 + 室C 床 1 + 室A室D 1
    assert s["目盛りの直し(作業 5)もつないだとき 確定する行数"] == 7  # + 室E


# --- 機械の確かめ(純粋な判定) ---------------------------------------------------------------


def test_plan_candidate_needs_same_orientation_spanning_label_and_not_other_rooms():
    reads = [{"id": "a", "値": 2000, "向き": "横", "始点": (0, 50), "終点": (100, 50)},
             {"id": "b", "値": 1500, "向き": "縦", "始点": (10, 0), "終点": (10, 100)},
             {"id": "c", "値": 1800, "向き": "横", "始点": (200, 50), "終点": (300, 50)}]
    assert [r["id"] for r in ru.plan_candidates([(50, 60)], reads, "横", [])] == ["a"]
    assert ru.plan_candidates([(50, 60)], reads, "横", [2000]) == []
    assert [r["id"] for r in ru.plan_candidates([(50, 60)], reads, "縦", [])] == ["b"]


def test_elevation_and_finish_candidates():
    assert ru.elevation_candidates([{"id": "x", "向き": "縦", "値": 2400}], ["2400", "12", "abc"], 2400) == []
    assert ru.elevation_candidates([{"id": "x", "向き": "横", "値": 3600}], [], 2400)
    assert ru.elevation_candidates([], ["3600"], 2400)
    assert ru.finish_candidates(["フローリング", "CH=", "2400"], 2400) == []
    assert ru.finish_candidates(["CH", "2500"], 2400) == []
    assert ru.finish_candidates(["6.5帖"], None) == ["面積・畳"]
    assert ru.finish_candidates(["12.3㎡"], None) == ["面積・畳"]
    assert ru.finish_candidates(["1800×2700"], None) == ["数x数"]
    assert ru.finish_candidates(["3600"], 2400) == ["3〜5 桁の数"]


def test_outline_owner():
    poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert ru.outline_owned(None, poly, set(), [(50, 50)], [])
    assert not ru.outline_owned(None, poly, set(), [(50, 50)], [(20, 20)])
    assert not ru.outline_owned(None, poly, set(), [(150, 50)], [])


def _search_pdf(path: Path, *, elevation_dim: bool) -> Path:
    doc = pymupdf.open()
    plan = doc.new_page(width=600, height=400)
    plan.insert_text((100, 100), "室A", fontname="japan")
    fin = doc.new_page(width=600, height=400)
    fin.insert_text((50, 100), "室A", fontname="japan")
    fin.insert_text((150, 100), "フローリング", fontname="japan")
    ele = doc.new_page(width=600, height=400)
    ele.insert_text((50, 50), "室A", fontname="japan")
    if elevation_dim:
        ele.insert_text((200, 200), "3600", fontname="helv")
    doc.save(path)
    return path


@pytest.mark.parametrize("elevation_dim", [False, True])
def test_search_room_on_a_synthetic_pdf(tmp_path, elevation_dim):
    pdf = _search_pdf(tmp_path / "s.pdf", elevation_dim=elevation_dim)
    drawing = ru.Drawing(pdf)
    room = dict(_room(0, ru.SIDE_ABSENT, ru.SIDE_ABSENT), _名前の語=["室A"], _鍵={"室A"})
    s = ru.search_room(drawing, room, [], {1: "平面図", 2: "仕上表", 3: "展開図"})
    assert s["平面図"]["結果"] == ru.FOUND_NONE
    assert s["仕上表"]["結果"] == ru.FOUND_NONE
    assert s["縮尺換算"]["結果"] == ru.FOUND_NONE  # 縮尺が決まらない
    assert s["展開図"]["結果"] == (ru.FOUND_CANDIDATE if elevation_dim else ru.FOUND_NONE)
    assert ru.numeric_allowed(s) is (not elevation_dim)
    # 名前が無い室は「見られなかった」
    other = dict(room, _名前の語=["室Z"], _鍵={"室Z"})
    s2 = ru.search_room(drawing, other, [], {1: "平面図", 2: "仕上表", 3: "展開図"})
    assert s2["平面図"]["結果"] == ru.NOT_SEARCHED and not ru.numeric_allowed(s2)


# --- 一本道(旗) --------------------------------------------------------------------------


def _tables(tmp_path: Path) -> tuple[Path, Path]:
    names = tmp_path / "室.json"
    names.write_text(json.dumps({"室": [{"室名": "洋室1", "横": [], "縦": [], "天井高_mm": None, "天井高のページ": None}]},
                                ensure_ascii=False), encoding="utf-8")
    sides = tmp_path / "辺.json"
    sides.write_text(json.dumps({"室": [{"番号": 0, "横": {"状態": "図面に書いていない"}, "縦": {"状態": "図面に書いていない"}}]},
                                ensure_ascii=False), encoding="utf-8")
    return names, sides


def test_flag_off_adds_nothing(tmp_path, machine_output):  # noqa: F811
    _, off = _run(tmp_path, "オフ", machine_output, FlagClient())
    assert "室の寸法が無い" not in off
    assert not any("分からない" in it for it in off["理解"]["項目"])
    assert not any("分からない" in r for r in off["組み立て"]["内訳の行"])


def test_flag_on_marks_rows_and_keeps_zero_auto(tmp_path, machine_output):  # noqa: F811
    names, sides = _tables(tmp_path)
    _, off = _run(tmp_path, "オフ", machine_output, FlagClient())
    answers = tmp_path / "答え.json"
    answers.write_text(json.dumps({"室の寸法:0": {"選択肢": ru.OPT_SITE}}, ensure_ascii=False), encoding="utf-8")
    code, on = _run(tmp_path, "オン", machine_output, FlagClient(), "--with-room-unknown", "--room-names", str(names),
                    "--room-sides", str(sides), "--room-answers", str(answers))
    assert code == 0
    sec = on["室の寸法が無い"]
    assert sec["C の行"] >= 1 and sec["書き出した寸法"] == 0
    assert all(r["数量"] is None and r["分からない"].startswith(ru.REASON) for r in sec["行"])
    assert sec["段階ごと"]["概算"] == []
    assert sec["検算(人の入力の行を足した)"]["自動確定"] == 0
    assert on["まとめ"]["自動確定"] == 0
    # 数量は旗オフと同じ(書き換えない)
    q = lambda r: [(it["id"], it["数量"]) for it in r["理解"]["項目"]]  # noqa: E731
    assert q(on) == q(off)
    marked = [it for it in on["理解"]["項目"] if "分からない" in it]
    assert marked and all(it["数量"] is None for it in marked)
    # 室の名前は出力の旗の欄に書かない
    assert "洋室1" not in json.dumps(sec, ensure_ascii=False)


def test_human_rows_through_the_production_gate_stay_unconfirmed():
    from draft.run import machine_check

    rows = [{"工事項目": "床 フローリング張替", "場所": "室A", "数量": 6.0, "単位": "m2", "メモ": ru.HUMAN, "項目": ["p1-001"]},
            {"工事項目": "幅木", "場所": "室A", "数量": 10.0, "単位": "m", "メモ": ru.HUMAN, "項目": ["p1-002"]}]
    assert machine_check(rows, None, None, "合成")["自動確定"] == 0


def test_card_pdf_has_room_numbers_options_and_inputs_only_when_allowed(tmp_path, rooms, items):
    rows = ru.c_rows(items, rooms)
    bad = {k: dict(v) for k, v in ALL_NONE.items()}
    bad["仕上表"]["結果"] = ru.FOUND_CANDIDATE
    qs = ru.build_questions(rows, rooms, {0: ALL_NONE, 1: bad, 2: ALL_NONE, 4: ALL_NONE}, len(items))
    path = ru.card_pdf(qs, tmp_path / "card.pdf")
    with pymupdf.open(path) as doc:
        assert doc.page_count == len(qs)
        for page, q in zip(doc, qs):
            text = page.get_text()
            assert f"室 {q['室']}" in text
            fields = [wd.field_name for wd in page.widgets()]
            texts = [f for f in fields if f.endswith("_mm")]
            assert len(texts) == len(q["数字の入力"])
            assert sum(1 for f in fields if not f.endswith("_mm")) == len(q["選択肢"])
