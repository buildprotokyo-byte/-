"""K-73 作業 3(a): 「読んだ文字が同じ」を対応づけの条件に足す(合成のデータだけ。正解は使わない。AI は呼ばない)。"""

from __future__ import annotations

import json

from draft import position_match as pm
from draft import split_cards

LEFT = (100.0, 100.0, 300.0, 160.0)
RIGHT = (1000.0, 100.0, 1200.0, 160.0)


def _it(rid: str, name: str, qty, *, unit: str = "箇所", room: str = "室A", page: int = 1,
        box=LEFT, elements=("e1",)) -> dict:
    return {"id": rid, "工事": name, "何": name, "場所": room, "部位": "", "品番": "", "数量": qty, "単位": unit,
            "ページ": page, "囲み": list(box) if box else None, "要素": list(elements), "状態": "観測", "確度": "中",
            "区分": "新設", "科目": "電気", "式": "", "読み取った値": "", "検算": [], "根拠の種類": "図面から読んだ"}


def _el(kind: str, box, text="甲") -> dict:
    return {"種類": kind, "位置": list(box), "内容": text}


def test_normalized_text():
    assert pm.normalized_text("Ａ Ｂ\n1") == "ab1"
    assert pm.normalized_text("  ") == ""
    assert pm.normalized_text(None) == ""
    assert pm.normalized_text(3) == ""


def test_same_element_with_text():
    a = _el("文字", LEFT, "ＡＢ　12")
    assert pm.same_element(a, _el("文字", LEFT, "ab12"), check_text=True)
    assert not pm.same_element(a, _el("文字", LEFT, "ab13"), check_text=True)       # 1 文字違いは同じでない
    assert pm.same_element(a, _el("文字", LEFT, "ab13"))                            # 既定は K-72 のまま
    assert not pm.same_element(_el("文字", LEFT, " "), _el("文字", LEFT, ""), check_text=True)  # 空は同じでない
    # 線にも同じ条件をかける。
    assert not pm.same_element(_el("線", LEFT, "甲"), _el("線", LEFT, "乙"), check_text=True)
    # 文字が同じでも、種類・箱が違えば同じでない(1〜5 は変えない)。
    assert not pm.same_element(_el("文字", LEFT), _el("記号", LEFT), check_text=True)
    assert not pm.same_element(_el("文字", LEFT), _el("文字", RIGHT), check_text=True)


def test_match_reason_same_pair():
    # 1〜5 を満たす組(p-q)と、文字が同じ組(p-r)が別々では対応づかない。
    idx_a = {(1, "p"): _el("文字", LEFT, "甲")}
    idx_b = {(1, "q"): _el("文字", LEFT, "乙"), (1, "r"): _el("文字", RIGHT, "甲")}
    a, b = _it("a", "x", 1, elements=("p",)), _it("b", "x", 1, elements=("q", "r"))
    assert pm.elements_match(a, b, idx_a, idx_b)
    assert not pm.elements_match(a, b, idx_a, idx_b, check_text=True)
    assert pm.match_reason(a, b, idx_a, idx_b, check_text=True) == pm.TEXT_MISMATCH
    assert pm.match_reason(a, _it("b", "x", 1, elements=("zz",)), idx_a, idx_b, check_text=True) == pm.ELEMENT_MISMATCH
    assert pm.match_reason(a, _it("b", "x", 1, elements=("q",)), idx_a, {(1, "q"): _el("文字", LEFT, "甲")},
                           check_text=True) is None


def test_chains_text_mismatch_status():
    rows = [[_it("a1", "x", 1, elements=("e1",))], [_it("b1", "x", 2, elements=("e1",))], []]
    idx = [{(1, "e1"): _el("文字", LEFT, "甲")}, {(1, "e1"): _el("文字", LEFT, "乙")}, {}]
    assert pm.chains(rows, elements=idx)["鎖"] == [[0, 0, None]]       # K-72 のやり方では対応づく
    got = pm.chains(rows, elements=idx, check_text=True)
    assert got["鎖"] == []
    assert got["行の行き先"] == {(0, 0): pm.TEXT_MISMATCH, (1, 0): pm.TEXT_MISMATCH}
    idx[1] = {(1, "e1"): _el("文字", LEFT, " 甲 ")}
    assert pm.chains(rows, elements=idx, check_text=True)["鎖"] == [[0, 0, None]]


def test_find_splits_reason_counted():
    runs = {"R1": [_it("a1", "照明の新設", 1, elements=("p",)), _it("a2", "照明の新設", 2, box=RIGHT, elements=("q",))],
            "R2": [_it("b1", "照明の新設", 3, elements=("p",)), _it("b2", "照明の新設", 4, box=RIGHT, elements=("q",))],
            "R3": [_it("c1", "照明の新設", 5, elements=("p",)), _it("c2", "照明の新設", 6, box=RIGHT, elements=("q",))]}
    idx = {r: {(1, "p"): _el("文字", LEFT, r), (1, "q"): _el("文字", RIGHT, r)} for r in runs}
    k72 = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    assert len(k72["カード"]) == 2          # 左右 2 つの鎖(K-72 のやり方)
    assert k72["割れた鍵の行き先"] == {"カード": 1}
    f = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx, text_match=True)
    assert f["カード"] == []
    assert f["割れた鍵の行き先"] == {pm.TEXT_MISMATCH: 1}


def _room_runs():
    runs = {
        "R1": [_it("a1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("a2", "照明の新設", 1, room="室B", box=RIGHT, elements=("q",))],
        "R2": [_it("b1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("b2", "照明の新設", 1, room="室C", box=RIGHT, elements=("q",))],
        "R3": [_it("c1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("c2", "照明の新設", 1, room="室B", box=RIGHT, elements=("q",))],
    }
    idx = {r: {(1, "p"): _el("文字", LEFT, "甲"), (1, "q"): _el("文字", RIGHT, "乙")} for r in runs}
    return runs, idx


def test_room_swap_with_elements_decoy_is_caught_by_text():
    from benchmarks import measure_k72_card_element as m

    runs, idx = _room_runs()
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx, text_match=True)
    assert [c["次元"] for c in found["カード"]] == ["室"]
    real = {"runs": runs, "found": found, "elements": idx}
    # K-72 のやり方では、要素ごと移すと誤る(2)。
    assert m.decoy(real, "別の室へ移す", with_elements=True)["回ごと"]["R2"]["誤って対応づけた行"] == 2
    # 読んだ文字も見ると、移した要素の文字は元のままなので、対応づかない。
    d = m.decoy(real, "別の室へ移す", with_elements=True, check_text=True)
    assert d["回ごと"]["R2"] == {"動かした行": 2, "誤って対応づけた行": 0, "移せなかった": 0}


def test_result_never_contains_text():
    from benchmarks import measure_k73_card_text as m

    runs, idx = _room_runs()
    secret = "秘密の読み"
    for r in idx:
        for k in idx[r]:
            idx[r][k]["内容"] = secret + k[1]
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx, text_match=True)
    real = {"runs": runs, "found": found, "elements": idx}
    out = m.text_counts(real)
    assert secret not in json.dumps(out, ensure_ascii=False)
    assert m.leaks_text(out, idx) == 0
    assert m.leaks_text({"x": [secret + "p"]}, idx) == 1
