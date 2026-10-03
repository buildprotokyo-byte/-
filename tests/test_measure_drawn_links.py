"""周1-2「作図者が書いた繋がりで切る」の試験。**合成データだけを使う。**"""

from __future__ import annotations

from benchmarks.measure_cluster_reading import Word
from benchmarks.measure_drawn_links import (
    MARK_MAX_SIZE,
    Mark,
    Segment,
    dimension_lines,
    pair_numbers,
)


def mark(x: float, y: float, size: float = 2.0, *, round_shape: bool = True) -> Mark:
    return Mark((x, y), size, round_shape)


def word(x0: float, y0: float, text: str, *, height: float = 10.0) -> Word:
    return Word((x0, y0, x0 + 20.0, y0 + height), text)


def test_両端に印がある線だけを寸法線とみなす() -> None:
    both = Segment((0.0, 0.0), (100.0, 0.0))
    one_end = Segment((0.0, 50.0), (100.0, 50.0))
    marks = [mark(0.0, 0.0), mark(100.0, 0.0), mark(0.0, 50.0)]
    assert dimension_lines([both, one_end], marks) == [both]


def test_印が無い線は寸法線にならない() -> None:
    assert dimension_lines([Segment((0.0, 0.0), (100.0, 0.0))], []) == []


def test_印は大きすぎると印にならない() -> None:
    """**印は文字より小さいもの**として見る(基準 3 節)。"""
    line = Segment((0.0, 0.0), (100.0, 0.0))
    big = [mark(0.0, 0.0, MARK_MAX_SIZE + 1), mark(100.0, 0.0, MARK_MAX_SIZE + 1)]
    # 大きい印は collect の側で落ちる。ここでは snap の距離だけを見る。
    assert dimension_lines([line], big) == [line]


def test_数字は中点にいちばん近い線と組になる() -> None:
    near = Segment((0.0, 0.0), (100.0, 0.0))
    far = Segment((0.0, 200.0), (100.0, 200.0))
    number = word(40.0, 5.0, "1000")
    pairs = pair_numbers([near, far], [number])
    assert list(pairs) == [0]


def test_遠い数字は組にならない() -> None:
    line = Segment((0.0, 0.0), (100.0, 0.0))
    number = word(40.0, 500.0, "1000")
    assert pair_numbers([line], [number]) == {}


def test_数字でない語は組にならない() -> None:
    line = Segment((0.0, 0.0), (100.0, 0.0))
    assert pair_numbers([line], [word(40.0, 5.0, "あいう")]) == {}


def test_1本の線に2つの数字が付くことがある() -> None:
    """**基準の線C が数えているのはこれである。**"""
    line = Segment((0.0, 0.0), (100.0, 0.0))
    pairs = pair_numbers([line], [word(40.0, 5.0, "1000"), word(45.0, 8.0, "2000")])
    assert len(pairs[0]) == 2


def test_組にする距離は文字の高さで決まる() -> None:
    """**紙の上の長さで持たないので、縮尺が変わっても同じしきいで動く。**"""
    line = Segment((0.0, 0.0), (100.0, 0.0))
    small = word(40.0, 25.0, "1000", height=5.0)
    large = word(40.0, 25.0, "1000", height=40.0)
    assert pair_numbers([line], [small]) == {}
    assert list(pair_numbers([line], [large])) == [0]


def test_線の長さと中点() -> None:
    line = Segment((0.0, 0.0), (30.0, 40.0))
    assert line.length == 50.0
    assert line.middle == (15.0, 20.0)
