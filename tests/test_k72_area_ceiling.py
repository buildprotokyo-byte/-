"""K-72 作業 C の分け方(`benchmarks/k72_area_ceiling.py`)。**合成のデータだけ**(室は作った名前)。"""

from __future__ import annotations

import pytest

from benchmarks import k72_area_ceiling as k


def key(text):
    return k.nfkc(text)


def _room(cat, ch=True, nr=False):
    return {"分け先": cat, "天井高": ch, "長方形でない": nr}


ROOMS = {
    "室A": _room(k.A),
    "室B": _room(k.B1),
    "室C": _room(k.C),
    "室D": _room(k.A, ch=False),
    "室E": _room(k.B2, nr=True),
    "室F": _room(k.B3),
}


def row(place="室A", part="床", unit="m2", qty=None, page=1):
    return {"場所": place, "部位": part, "単位": unit, "数量": qty, "ページ": page}


def classify(it):
    return k.classify_row(it, ROOMS, key=key)


def test_order_d1_first():
    assert classify(row(place="未確定"))[0] == k.D1
    assert classify(row(place=""))[0] == k.D1
    assert classify(row(place="室Z"))[0] == k.D1
    # 10 室以外の語を 1 つでも含めば D1(結べた室だけで出さない)
    assert classify(row(place="室A・室Z"))[0] == k.D1


def test_d2_parts_not_from_room_dimensions():
    assert classify(row(part="電気", unit="m"))[0] == k.D2
    assert classify(row(part="造作", unit="m"))[0] == k.D2
    assert classify(row(part="壁", unit="m"))[0] == k.D2
    assert classify(row(part="床", unit="m"))[0] == k.D2
    # 室に結べない行は D2 より先に D1
    assert classify(row(place="室Z", part="電気", unit="m"))[0] == k.D1


def test_room_categories_and_units():
    assert classify(row())[0] == k.A
    assert classify(row(unit="㎡"))[0] == k.A
    assert classify(row(part="天井"))[0] == k.A
    assert classify(row(part="幅木", unit="m"))[0] == k.A
    assert classify(row(place="室B"))[0] == k.B1
    assert classify(row(place="室C"))[0] == k.C


def test_wall_without_printed_ceiling_is_c():
    cat, marks = classify(row(place="室D", part="壁"))
    assert cat == k.C and marks["天井高が無い"]
    # 床は天井高が要らない
    assert classify(row(place="室D", part="床"))[0] == k.A
    cat, marks = classify(row(place="室A", part="壁"))
    assert cat == k.A and marks["開口を引かない"]


def test_multi_room_takes_worst():
    assert classify(row(place="室A・室B"))[0] == k.B1
    assert classify(row(place="室A・室C"))[0] == k.C
    assert classify(row(place="室B 室F"))[0] == k.B3


def test_not_rectangle_mark():
    cat, marks = classify(row(place="室E"))
    assert cat == k.B2 and marks["長方形でない"]


def test_room_category_never_a_when_absent_or_not_rectangle():
    R, AB = k.SIDE_READ, k.SIDE_ABSENT
    assert k.room_category({"横": R, "縦": R}) == k.A
    assert k.room_category({"横": R, "縦": AB}) == k.C
    assert k.room_category({"横": k.SIDE_NEEDS_AI, "縦": AB}) == k.C
    assert k.room_category({"横": R, "縦": R}, not_rectangle=True) == k.B2
    assert k.room_category({"横": R, "縦": k.SIDE_RULER}) == k.B1
    assert k.room_category({"横": k.SIDE_NEEDS_AI, "縦": k.SIDE_RULER}) == k.B2
    assert k.room_category({"横": k.SIDE_NEEDS_AI, "縦": k.SIDE_UNREADABLE}) == k.B3


def test_side_state_moves_by_value_only_when_unique():
    plain = [{"id": "P1-D1", "値": 1000.0, "向き": "横"}, {"id": "P1-D2", "値": 2000.0, "向き": "縦"},
             {"id": "P1-D3", "値": 2000.0, "向き": "縦"}]
    ruler = plain + [{"id": "P1-D4", "値": 3000.0, "向き": "縦"}, {"id": "P1-D5", "値": 777.0, "向き": "横"}]
    mapped = lambda v: {"状態": k.COPY_MAPPED, "値": [v]}  # noqa: E731
    assert k.side_state(mapped(1000), "横", plain, ruler) == k.SIDE_READ
    assert k.side_state(mapped(1000), "縦", plain, ruler) == k.SIDE_UNREADABLE  # 向きが違う
    assert k.side_state(mapped(2000), "縦", plain, ruler) == k.SIDE_UNREADABLE  # 2 つあって決まらない
    assert k.side_state(mapped(3000), "縦", plain, ruler) == k.SIDE_RULER
    assert k.side_state({"状態": k.COPY_ABSENT}, "縦", plain, ruler) == k.SIDE_ABSENT
    assert k.side_state({"状態": k.COPY_UNREAD, "値の候補": [777]}, "縦", plain, ruler) == k.SIDE_NEEDS_AI
    assert k.side_state({"状態": k.COPY_UNREAD, "値の候補": [777, 999]}, "縦", plain, ruler) == k.SIDE_UNREADABLE
    with pytest.raises(ValueError):
        k.side_state({"状態": "?"}, "縦", plain, ruler)


def test_count_rows_each_row_once_and_other_units():
    items = [row(), row(place="室C"), row(place="未確定"), row(part="電気", unit="m"),
             row(unit="式"), row(unit=""), row(unit="個"), row(qty=3.0)]
    out = k.count_rows(items, ROOMS, {1: "平面図"}, key=key)
    assert out["数量が無い項目"] == 7
    assert out["面積・長さの行"] == 4
    assert out["面積・長さでない行"] == 3
    assert out["面積・長さでない行(単位ごと)"] == {"式": 1, "(空欄)": 1, "個": 1}
    assert out["1行1つの確かめ(分け先の和 = 面積・長さの行)"]
    assert out["分け先"][k.A] == 1 and out["分け先"][k.C] == 1
    assert out["分け先"][k.D1] == 1 and out["分け先"][k.D2] == 1
    assert out["ページの種類ごと"][k.A] == {"平面図": 1}
    # 数量のある行は数えない(未取得を 0 にしない・数量を作らない)
    assert out["まとめ"]["数量にできる(A)"] == 1


def test_non_area_row_rejected_by_classify():
    with pytest.raises(ValueError):
        classify(row(unit="式"))
