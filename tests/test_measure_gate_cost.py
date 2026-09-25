"""周38 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

import benchmarks.measure_gate_cost as m
from axes.reading.meaning import PHASE_UNKNOWN
from intake.start_kit import (
    LEGEND_PAGE_KINDS,
    REPEATED_SYMBOL_PAGE_KINDS,
    ROOM_OUTLINE_PAGE_KINDS,
)


def test_設備図は記号を開いて室の輪郭を閉じる():
    """**この 1 件が、2 つの関門を分けられる理由である。**"""
    関門 = m.gates_for("設備図")
    assert 関門["繰り返す記号"] == "開く"
    assert 関門["室の輪郭"] == "閉じる"


def test_平面図はどちらも開く():
    関門 = m.gates_for("平面図")
    assert 関門["繰り返す記号"] == "開く"
    assert 関門["室の輪郭"] == "開く"


def test_その他はどちらも閉じる():
    関門 = m.gates_for("その他")
    assert 関門["繰り返す記号"] == "閉じる"
    assert 関門["室の輪郭"] == "閉じる"


def test_凡例は止まるのではなく別の道へ入る():
    assert m.gates_for("凡例")["繰り返す記号"] == "凡例として読む別の道"


def test_関門はコードの定数から引いている():
    """**手で書いた表ではない。**定数が変われば、この道具の答えも変わる。"""
    assert "設備図" in REPEATED_SYMBOL_PAGE_KINDS
    assert "設備図" not in ROOM_OUTLINE_PAGE_KINDS
    assert "凡例" in LEGEND_PAGE_KINDS


def test_記号だけを閉じる種類は無い():
    """**だから記号の値段は引き算でしか出ない**(基準 3 節・6 節)。"""
    記号だけ閉じる = [
        kind
        for kind in ROOM_OUTLINE_PAGE_KINDS
        if kind not in REPEATED_SYMBOL_PAGE_KINDS
    ]
    assert 記号だけ閉じる == []


def test_線1の合格はその他で失われた件数の半分():
    assert m.LOST_BY_OTHER == 2887 - 166
    assert m.LINE1_MAX_LOST == m.LOST_BY_OTHER / 2


def test_宣言は位相を決めない():
    """**周38 は種類だけを変える。**位相は周36・周37 の問い。

    `PageDeclaration.phase` の既定は `不明`(「決まっていない」という記録)で、
    `None` ではない。**ここで値を入れない**ことが、この周の条件である。
    """
    for 宣言 in m.declarations_for(3, "設備図"):
        assert 宣言.phase == PHASE_UNKNOWN
        assert 宣言.kind == "設備図"


def test_宣言は全ページぶん作る():
    assert [d.page_number for d in m.declarations_for(4, "凡例")] == [1, 2, 3, 4]
