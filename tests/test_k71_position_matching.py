"""K-71 作業 2: 位置の重なりで 3 回の行を対応づける(合成のデータだけ。正解は使わない。AI は呼ばない)。"""

from __future__ import annotations

from draft import position_match as pm
from draft import split_cards


def _it(rid: str, name: str, qty, unit: str = "箇所", room: str = "室A", page: int = 1,
        box=(100.0, 100.0, 300.0, 160.0), elements=("e1",)) -> dict:
    return {"id": rid, "工事": name, "何": name, "場所": room, "部位": "", "品番": "", "数量": qty, "単位": unit,
            "ページ": page, "囲み": list(box) if box else None, "要素": list(elements), "状態": "観測", "確度": "中",
            "区分": "新設", "科目": "電気", "式": "", "読み取った値": "", "検算": [], "根拠の種類": "図面から読んだ"}


LEFT = (100.0, 100.0, 300.0, 160.0)
RIGHT = (1000.0, 100.0, 1200.0, 160.0)


def test_iou_and_page():
    a = _it("a", "x", 1)
    assert pm.overlap(a, _it("b", "x", 1)) == 1.0
    assert pm.overlap(a, _it("b", "x", 1, page=2)) == 0.0
    # ひっくり返った四角も直してから比べる。
    assert pm.overlap(a, _it("b", "x", 1, box=(300.0, 160.0, 100.0, 100.0))) == 1.0
    assert pm.overlap(a, _it("b", "x", 1, box=None)) == 0.0
    # 半分ずらすと 1/3。
    assert abs(pm.iou(LEFT, (200.0, 100.0, 400.0, 160.0)) - 1 / 3) < 1e-9


def test_two_rows_in_a_run_are_matched_by_position():
    rows = [[_it("a1", "コンセント", 3, box=LEFT), _it("a2", "コンセント", 2, box=RIGHT)],
            [_it("b1", "コンセント", 4, box=LEFT), _it("b2", "コンセント", 2, box=RIGHT)],
            [_it("c1", "コンセント", 3, box=(110.0, 100.0, 310.0, 160.0)), _it("c2", "コンセント", 2, box=RIGHT)]]
    got = pm.chains(rows)
    assert sorted(got["鎖"]) == [[0, 0, 0], [1, 1, 1]]
    assert pm.check_chains(rows, got["鎖"]) == {"回の行を 2 行以上持つ鎖": 0, "2 つ以上の鎖に入った行": 0}


def test_tie_is_not_matched():
    # 同じ箱の行が 2 つずつ(要素も同じ)→ 同点で決まらない。どちらかに寄せない。
    rows = [[_it("a1", "x", 1), _it("a2", "x", 2)], [_it("b1", "x", 3), _it("b2", "x", 4)], []]
    got = pm.chains(rows)
    assert got["鎖"] == []
    assert set(got["行の行き先"].values()) == {pm.TIED}
    # 要素の重なりが違えば分けられる。
    rows = [[_it("a1", "x", 1, elements=("p",)), _it("a2", "x", 2, elements=("q",))],
            [_it("b1", "x", 3, elements=("q",)), _it("b2", "x", 4, elements=("p",))], []]
    got = pm.chains(rows)
    assert sorted(got["鎖"]) == [[0, 1, None], [1, 0, None]]


def test_r2_r3_are_matched_when_base_has_no_partner():
    rows = [[_it("a1", "x", 1, box=LEFT)], [_it("b1", "x", 2, box=RIGHT)], [_it("c1", "x", 3, box=RIGHT)]]
    got = pm.chains(rows)
    assert got["鎖"] == [[None, 0, 0]]
    assert got["行の行き先"][(0, 0)] == pm.NO_PARTNER


def _dup_runs():
    return {
        "R1": [_it("a1", "コンセントの新設", 3, box=LEFT), _it("a2", "コンセントの新設", 2, box=RIGHT)],
        "R2": [_it("b1", "コンセントの新設", 4, box=LEFT), _it("b2", "コンセントの新設", 2, box=RIGHT)],
        "R3": [_it("c1", "コンセントの新設", 3, box=LEFT), _it("c2", "コンセントの新設", 2, unit="個", box=RIGHT)],
    }


def test_default_is_unchanged_and_position_makes_a_card():
    runs = _dup_runs()
    old = split_cards.find_splits(runs)
    assert old["カード"] == []
    assert [x["理由"] for x in old["カードにできなかった組"]] == [split_cards.REASONS[0]]
    new = split_cards.find_splits(runs, match="位置", unit_equivalence=True)
    assert len(new["カード"]) == 1
    card = new["カード"][0]
    assert split_cards.real_options(card) == ["3箇所", "4箇所"]
    assert set(card["行"].values()) == {"a1", "b1", "c1"}
    # 右の鎖は「2 箇所」と「2 個」だけ → 単位の同値で外した。
    assert len(new["単位の同値で外したカード"]) == 1
    assert new["割れた鍵の行き先"] == {"カード": 1}
    assert new["対応づけ"]["鎖"] == 2


def test_unit_equivalence_keeps_shiki_and_counts_drops():
    def runs(u2: str) -> dict:
        return {"R1": [_it("a", "照明器具の新設", 1, unit="台")], "R2": [_it("b", "照明器具の新設", 1, unit=u2)],
                "R3": [_it("c", "照明器具の新設", 1, unit="台")]}

    dropped = split_cards.find_splits(runs("個"), match="位置", unit_equivalence=True)
    assert dropped["カード"] == [] and len(dropped["単位の同値で外したカード"]) == 1
    assert dropped["割れた鍵の行き先"] == {split_cards.UNIT_DROPPED: 1}
    # 式は同値に入れない → 残る。
    kept = split_cards.find_splits(runs("式"), match="位置", unit_equivalence=True)
    assert len(kept["カード"]) == 1
    # 本・組・セットも入れない。
    for u in ("本", "組"):
        assert len(split_cards.find_splits(runs(u), match="位置", unit_equivalence=True)["カード"]) == 1
    # 同値を使わなければ前の周と同じくカードになる。
    assert len(split_cards.find_splits(runs("個"))["カード"]) == 1


def test_same_by_new_unit_merges_only_equal_numbers():
    assert split_cards.same_by_new_unit(["1個", "1台", "2個"]) == ["1個", "2個"]
    assert split_cards.same_by_new_unit(["1個", "1式"]) == ["1個", "1式"]
    assert split_cards.same_by_new_unit(["1m", "1m2"]) == ["1m", "1m2"]
    assert split_cards.same_by_new_unit(["1か所", "1枚"]) == ["1か所"]


def test_partial_merge_keeps_card_and_sources():
    runs = {"R1": [_it("a", "x器具の新設", 1, unit="台")], "R2": [_it("b", "x器具の新設", 1, unit="個")],
            "R3": [_it("c", "x器具の新設", 2, unit="台")]}
    f = split_cards.find_splits(runs, match="位置", unit_equivalence=True)
    card = f["カード"][0]
    # 揃えると同じになる選択肢は、並べた順で先の書き方 1 つにまとめる(出どころは両方残す)。
    assert split_cards.real_options(card) == ["1個", "2台"]
    assert {s["辿る"]["回"] for s in card["値の出どころ"]["1個"]} == {"R1", "R2"}


def test_decoys_count_wrong_matches():
    from benchmarks import measure_k71_position_matching as m

    runs = {"R1": [], "R2": [], "R3": []}
    for i in range(12):
        x = 100.0 + i * 600 % 1800
        y = 100.0 + (i // 3) * 400
        box = (x, y, x + 200, y + 60)
        for r, p in (("R1", "a"), ("R2", "b"), ("R3", "c")):
            runs[r].append(_it(f"{p}{i}", "コンセントの新設", 1 + (r == "R2"), room=f"室{i % 3}", box=box))
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True)
    real = {"runs": runs, "found": found}
    shift = m.decoy(real, "ずらす")
    assert shift["動かした行(和)"] == 24
    assert shift["誤って対応づけた行(和)"] == 0
    assert shift["判定"] == "合格"
    move = m.decoy(real, "別の室へ移す")
    # 室ごとに鍵が違う(別の組)ので、別の室の位置へ移しても同じ組の行とは重ならない。
    assert move["動かした行(和)"] + move["移せなかった行(和)"] == 24
    assert move["誤って対応づけた行(和)"] == 0
    assert move["誤って対応づけた割合"][1] == move["動かした行(和)"]


def test_room_decoy_catches_a_swap():
    from benchmarks import measure_k71_position_matching as m

    runs = {
        "R1": [_it("a1", "照明の新設", 1, room="室A", box=LEFT), _it("a2", "照明の新設", 1, room="室B", box=RIGHT)],
        "R2": [_it("b1", "照明の新設", 1, room="室A", box=LEFT), _it("b2", "照明の新設", 1, room="室C", box=RIGHT)],
        "R3": [_it("c1", "照明の新設", 1, room="室A", box=LEFT), _it("c2", "照明の新設", 1, room="室B", box=RIGHT)],
    }
    found = split_cards.find_splits(runs, match="位置", unit_equivalence=True)
    assert [c["次元"] for c in found["カード"]] == ["室"]
    real = {"runs": runs, "found": found}
    move = m.decoy(real, "別の室へ移す")
    # 2 回目の 2 行が入れ替わる位置へ移る → 位置しか見ないので、入れ替わった先の行と対応づく(誤り 2)。
    assert move["回ごと"]["R2"] == {"動かした行": 2, "誤って対応づけた行": 2, "移せなかった": 0}
    assert move["判定"] == "判定できない"  # 動かした行が 20 未満
