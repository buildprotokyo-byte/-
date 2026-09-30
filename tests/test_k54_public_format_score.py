"""K-54 の数える道具を、合成の対応で確かめる(実際の正解は使わない)。"""

from benchmarks.k54_public_format_score import frames, judge, score


def _r(kamoku, name, qty, *, kind="", unit="㎡", middle=""):
    return {"科目": kamoku, "中科目": middle, "区分": kind, "工事項目": name, "数量": qty, "単位": unit}


def test_split_rows_that_add_up_are_consistent() -> None:
    item = {"数量": 10.0, "単位": "㎡", "候補": [[_r("内装改修", "床", 6.0, kind="新設"), _r("内装改修", "床下地", 4.0, kind="新設")]]}
    out = judge(item)
    assert out["割れた"] and out["数量が整合"]


def test_split_rows_that_each_carry_the_common_quantity_are_consistent() -> None:
    item = {"数量": 3.0, "単位": "箇所", "候補": [[_r("木工事", "枠", 3.0, unit="箇所"), _r("塗装", "枠塗装", 3.0, unit="箇所")]]}
    assert judge(item)["数量が整合"]


def test_a_missing_quantity_is_not_added_as_zero() -> None:
    item = {"数量": 10.0, "単位": "㎡", "候補": [[_r("内装改修", "床", None), _r("内装改修", "床", 10.0)]]}
    assert not judge(item)["数量が整合"]


def test_any_of_several_candidates_counts() -> None:
    item = {"数量": 2.0, "単位": "箇所", "候補": [[_r("木工事", "棚", 5.0, unit="箇所")], [_r("家具", "棚", 2.0, unit="箇所")]]}
    out = judge(item)
    assert out["複数候補"] and out["数量が整合"]


def test_an_item_without_a_place_is_counted_as_not_placed() -> None:
    assert not judge({"数量": 1.0, "単位": "式", "候補": []})["置けた"]


def test_score_builds_the_breakdown_and_counts_frames() -> None:
    mapping = {
        "G1": {"数量": 5.0, "単位": "㎡", "候補": [[_r("内装改修", "床撤去", 5.0, kind="撤去")]]},
        "G2": {"数量": 5.0, "単位": "㎡", "候補": [[_r("内装改修", "床", 5.0, kind="新設")]]},
        "G3": {"数量": 1.0, "単位": "台", "候補": [[_r("電気設備", "照明器具", 1.0, kind="撤去", unit="台")]]},
    }
    out = score(mapping)
    assert out["公開書式に置けた"] == 3
    assert out["中科目が立った科目"] == ["内装改修"]
    assert out["空の枠"] == 0


def test_frames_counts_empty_frames() -> None:
    book = {"種目": [{"科目": [{"名称": "A", "中科目": [{"名称": "撤去", "細目": []}]}, {"名称": "B", "細目": []}]}]}
    assert frames(book) == {"中科目が立った科目": ["A"], "空の枠": 2}
