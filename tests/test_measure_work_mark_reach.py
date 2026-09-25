"""周34 の測定道具のテスト。**合成のデータだけを使う。実図面も実際の凡例も使わない。**"""

from __future__ import annotations

from types import SimpleNamespace

from axes.image_axis.legend_lookup import BINDING_CASE_LEGEND, LegendTable
from benchmarks.measure_work_mark_reach import (
    ROUND33_DECOY,
    ROUND33_REAL,
    pages_with,
    split_words,
    work_mark_codes,
)
from benchmarks.measure_phase_word_proximity import DISTANCES_MM


def table(**rows) -> LegendTable:
    payload = {"binding": BINDING_CASE_LEGEND}
    payload.update(rows)
    return LegendTable.from_payload(payload)


class FakePage:
    """`get_text("words")` だけを返す作り物のページ。"""

    def __init__(self, words) -> None:
        self._words = words

    def get_text(self, kind: str):
        assert kind == "words"
        return self._words


def test_印は対照表のコードから取る() -> None:
    got = work_mark_codes(table(work_marks=[{"code": "AA", "meaning": "あ"}]))
    assert got == {"AA"}


def test_意味の無い行は印にしない() -> None:
    # 読めなかった行を印として数えると、当たるはずのない語が当たる。
    got = work_mark_codes(table(work_marks=[{"code": "AA", "meaning": ""}]))
    assert got == frozenset()


def test_コードの無い行は印にしない() -> None:
    got = work_mark_codes(table(work_marks=[{"code": "", "meaning": "あ"}]))
    assert got == frozenset()


def test_印と印以外に分かれる() -> None:
    page = FakePage([(0, 0, 2, 2, "AA", 0, 0, 0), (10, 10, 12, 12, "ZZ", 0, 0, 1)])
    marks, others = split_words(page, frozenset({"AA"}))
    assert marks == [(1.0, 1.0)]
    assert others == [(11.0, 11.0)]


def test_空白だけの語は数えない() -> None:
    page = FakePage([(0, 0, 2, 2, "   ", 0, 0, 0)])
    marks, others = split_words(page, frozenset({"AA"}))
    assert marks == [] and others == []


def test_全角と半角の違いで印を取りこぼさない() -> None:
    page = FakePage([(0, 0, 2, 2, "ＡＡ", 0, 0, 0)])
    marks, _others = split_words(page, frozenset({"AA"}))
    assert marks == [(1.0, 1.0)]


def test_印が無いページは囮の引き出しだけになる() -> None:
    page = FakePage([(0, 0, 2, 2, "ZZ", 0, 0, 0)])
    marks, others = split_words(page, frozenset({"AA"}))
    assert marks == []
    assert len(others) == 1


def test_空でないページだけを数える() -> None:
    assert pages_with({1: [(0.0, 0.0)], 2: []}) == {1}


def test_周33の数字は3段そろえて持っている() -> None:
    # 報告に並べて載せるためだけの数字。判定には使わない。
    assert set(ROUND33_REAL) == set(DISTANCES_MM)
    assert set(ROUND33_DECOY) == set(DISTANCES_MM)


def test_周33の数字は囮のほうが大きい() -> None:
    # 周33 は 3 段とも囮が勝った。写し間違えたらここで落ちる。
    for limit in DISTANCES_MM:
        assert ROUND33_DECOY[limit] > ROUND33_REAL[limit]
