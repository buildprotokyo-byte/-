"""K-72 作業 A: 位置の重なりに加えて、要素の重なりも見る(合成のデータだけ。正解は使わない。AI は呼ばない)。"""

from __future__ import annotations

from draft import position_match as pm
from draft import split_cards

LEFT = (100.0, 100.0, 300.0, 160.0)
RIGHT = (1000.0, 100.0, 1200.0, 160.0)


def _it(rid: str, name: str, qty, *, unit: str = "箇所", room: str = "室A", page: int = 1,
        box=LEFT, elements=("e1",)) -> dict:
    return {"id": rid, "工事": name, "何": name, "場所": room, "部位": "", "品番": "", "数量": qty, "単位": unit,
            "ページ": page, "囲み": list(box) if box else None, "要素": list(elements), "状態": "観測", "確度": "中",
            "区分": "新設", "科目": "電気", "式": "", "読み取った値": "", "検算": [], "根拠の種類": "図面から読んだ"}


def _el(kind: str, box) -> dict:
    return {"種類": kind, "位置": list(box)}


def test_same_element_needs_kind_size_and_overlap():
    a = _el("文字", (100, 100, 300, 160))
    assert pm.same_element(a, _el("文字", (110, 105, 310, 165)))
    assert not pm.same_element(a, _el("記号", (110, 105, 310, 165)))          # 種類が違う
    assert not pm.same_element(a, _el("文字", (100, 100, 190, 160)))          # 幅が 2 倍より違う(90 / 200)
    assert pm.same_element(a, _el("文字", (100, 100, 200, 160)))              # ちょうど 2 倍は近い
    assert not pm.same_element(a, _el("文字", (400, 100, 600, 160)))          # 箱が重ならない
    # 高さ 0 の線は 1 画素として比べる。
    line = _el("線", (100, 50, 300, 50))
    assert pm.same_element(line, _el("線", (100, 50, 300, 50)))
    assert not pm.same_element(line, _el("線", (100, 50, 300, 53)))           # 高さ 1 と 3
    # 大きさの許容を変えられる(参考の数のため)。
    assert pm.same_element(a, _el("文字", (100, 100, 190, 160)), size_ratio=0.4)


def test_elements_match_rows():
    idx = [{(1, "e1"): _el("文字", LEFT)}, {(1, "e1"): _el("文字", LEFT), (1, "e2"): _el("図", LEFT)}]
    a, b = _it("a", "x", 1), _it("b", "x", 1)
    assert pm.elements_match(a, b, idx[0], idx[1])
    assert not pm.elements_match(a, _it("b", "x", 1, elements=("e2",)), idx[0], idx[1])  # 種類が違う
    assert not pm.elements_match(a, _it("b", "x", 1, elements=("zz",)), idx[0], idx[1])  # 要素が引けない
    assert not pm.elements_match(a, _it("b", "x", 1, page=2), idx[0], idx[1])            # ページが違う
    # 室が違えば重ならない。室の組では室を見ない。
    assert not pm.elements_match(a, _it("b", "x", 1, room="室B"), idx[0], idx[1])
    assert pm.elements_match(a, _it("b", "x", 1, room="室B"), idx[0], idx[1], check_room=False)


def test_chains_reject_position_only_match_and_say_why():
    rows = [[_it("a1", "x", 1, elements=("e1",))], [_it("b1", "x", 2, elements=("e1",))], []]
    idx = [{(1, "e1"): _el("文字", LEFT)}, {(1, "e1"): _el("記号", LEFT)}, {}]
    # 位置だけなら対応づく(K-71 と同じ)。
    assert pm.chains(rows)["鎖"] == [[0, 0, None]]
    got = pm.chains(rows, elements=idx)
    assert got["鎖"] == []
    assert got["行の行き先"] == {(0, 0): pm.ELEMENT_MISMATCH, (1, 0): pm.ELEMENT_MISMATCH}
    # 種類が同じなら対応づく。
    idx[1] = {(1, "e1"): _el("文字", (105, 100, 305, 160))}
    assert pm.chains(rows, elements=idx)["鎖"] == [[0, 0, None]]


def test_element_check_picks_the_row_whose_elements_agree():
    # 2 回目に同じ箱の行が 2 つ。位置だけなら同点だが、要素で 1 つに決まる。
    rows = [[_it("a1", "x", 1, elements=("p",))],
            [_it("b1", "x", 2, elements=("q",)), _it("b2", "x", 3, elements=("r",))], []]
    idx = [{(1, "p"): _el("文字", LEFT)},
           {(1, "q"): _el("記号", LEFT), (1, "r"): _el("文字", LEFT)}, {}]
    got = pm.chains(rows, elements=idx)
    assert got["鎖"] == [[0, 1, None]]
    assert got["行の行き先"][(1, 0)] == pm.ELEMENT_MISMATCH


def _runs_and_index(kind2: str = "文字"):
    runs = {"R1": [_it("a1", "コンセントの新設", 3, box=LEFT, elements=("p",)),
                   _it("a2", "コンセントの新設", 2, box=RIGHT, elements=("q",))],
            "R2": [_it("b1", "コンセントの新設", 4, box=LEFT, elements=("p",)),
                   _it("b2", "コンセントの新設", 2, box=RIGHT, elements=("q",))],
            "R3": [_it("c1", "コンセントの新設", 3, box=LEFT, elements=("p",)),
                   _it("c2", "コンセントの新設", 2, unit="個", box=RIGHT, elements=("q",))]}
    idx = {r: {(1, "p"): _el("文字", LEFT), (1, "q"): _el("記号", RIGHT)} for r in runs}
    idx["R2"][(1, "p")] = _el(kind2, LEFT)
    return runs, idx


def test_find_splits_default_unchanged_and_reason_counted():
    runs, idx = _runs_and_index()
    k71 = split_cards.find_splits(runs, match="位置", unit_equivalence=True)
    same = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    assert len(same["カード"]) == len(k71["カード"]) == 1
    assert same["割れた鍵の行き先"] == k71["割れた鍵の行き先"] == {"カード": 1}
    # 2 回目の左の行だけ要素の種類が違う → 左の鎖は 1 回目と 3 回目の「3」だけで値が 1 つしか無い。
    # 右の鎖は単位の同値で外れる(鍵は上の入れ先の「単位の同値で外した」に入る)。カードは減る。
    runs, idx = _runs_and_index("図")
    f = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    assert f["カード"] == []
    assert f["対応づけ"]["行の行き先"].get(pm.ELEMENT_MISMATCH) == 1
    assert f["割れた鍵の行き先"] == {split_cards.UNIT_DROPPED: 1}


def test_element_mismatch_is_a_reason_when_nothing_else():
    runs = {"R1": [_it("a1", "照明の新設", 1, elements=("p",)), _it("a2", "照明の新設", 2, box=RIGHT, elements=("q",))],
            "R2": [_it("b1", "照明の新設", 3, elements=("p",)), _it("b2", "照明の新設", 4, box=RIGHT, elements=("q",))],
            "R3": [_it("c1", "照明の新設", 5, elements=("p",)), _it("c2", "照明の新設", 6, box=RIGHT, elements=("q",))]}
    idx = {"R1": {(1, "p"): _el("文字", LEFT), (1, "q"): _el("文字", RIGHT)},
           "R2": {(1, "p"): _el("図", LEFT), (1, "q"): _el("図", RIGHT)},
           "R3": {(1, "p"): _el("写真", LEFT), (1, "q"): _el("写真", RIGHT)}}
    f = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    assert f["カード"] == []
    assert f["割れた鍵の行き先"] == {pm.ELEMENT_MISMATCH: 1}


def test_room_swap_decoy_is_caught_by_elements():
    from benchmarks import measure_k72_card_element as m

    runs = {
        "R1": [_it("a1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("a2", "照明の新設", 1, room="室B", box=RIGHT, elements=("q",))],
        "R2": [_it("b1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("b2", "照明の新設", 1, room="室C", box=RIGHT, elements=("q",))],
        "R3": [_it("c1", "照明の新設", 1, room="室A", box=LEFT, elements=("p",)),
               _it("c2", "照明の新設", 1, room="室B", box=RIGHT, elements=("q",))],
    }
    idx = {r: {(1, "p"): _el("文字", LEFT), (1, "q"): _el("文字", RIGHT)} for r in runs}
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    assert [c["次元"] for c in found["カード"]] == ["室"]
    real = {"runs": runs, "found": found, "elements": idx}
    # K-71 と同じ形(箱だけ動かす): 要素は元の場所に残るので、対応づかない。
    move = m.decoy(real, "別の室へ移す", with_elements=False)
    assert move["回ごと"]["R2"] == {"動かした行": 2, "誤って対応づけた行": 0, "移せなかった": 0}
    # 要素ごと動かす: 移した先に同じ種類・同じ大きさの要素があれば、位置からは見分けられない(誤り 2)。
    move2 = m.decoy(real, "別の室へ移す", with_elements=True)
    assert move2["回ごと"]["R2"] == {"動かした行": 2, "誤って対応づけた行": 2, "移せなかった": 0}
    # 位置だけ(K-71)で同じ囮を測ると、箱だけでも誤る。
    k71 = m.decoy(real, "別の室へ移す", with_elements=False, element_check=False)
    assert k71["回ごと"]["R2"]["誤って対応づけた行"] == 2


def test_shift_decoy_with_elements():
    from benchmarks import measure_k72_card_element as m

    runs = {"R1": [], "R2": [], "R3": []}
    idx = {r: {} for r in runs}
    for i in range(4):
        box = (100.0 + 300 * i, 100.0 + 300 * i, 300.0 + 300 * i, 160.0 + 300 * i)
        for r, p in (("R1", "a"), ("R2", "b"), ("R3", "c")):
            runs[r].append(_it(f"{p}{i}", "コンセントの新設", 1 + (r == "R2"), box=box, elements=(f"e{i}",)))
            idx[r][(1, f"e{i}")] = _el("文字" if i % 2 else "記号", box)
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True, elements=idx)
    real = {"runs": runs, "found": found, "elements": idx}
    # 右へ 300・下へ 300 ずらすと隣の行の箱にぴったり重なる。要素の種類が交互なので、要素ごと動かしても対応づかない。
    for w in (False, True):
        d = m.decoy(real, "ずらす", with_elements=w)
        assert d["動かした行(和)"] == 8
        assert d["誤って対応づけた行(和)"] == 0
    # 位置だけなら、ずらした先の行と対応づく(右端の行を除く 3 行 × 2 回)。
    k71 = m.decoy(real, "ずらす", with_elements=True, element_check=False)
    assert k71["誤って対応づけた行(和)"] == 6
