"""周1-4「閉じた輪郭の内と外で切る」の試験。**合成データだけを使う。**"""

from __future__ import annotations

import random

from benchmarks.measure_cluster_reading import Word
from benchmarks.measure_closed_faces import _count, _inside, _shift

SQUARE = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))


def word(x0: float, y0: float, text: str) -> Word:
    return Word((x0, y0, x0 + 10.0, y0 + 10.0), text)


def test_内側の点は内側と判定される() -> None:
    assert _inside((50.0, 50.0), SQUARE) is True


def test_外側の点は外側と判定される() -> None:
    assert _inside((150.0, 50.0), SQUARE) is False
    assert _inside((50.0, -1.0), SQUARE) is False


def test_凹んだ形でも内と外が分かれる() -> None:
    l_shape = ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (40.0, 40.0), (40.0, 100.0), (0.0, 100.0))
    assert _inside((20.0, 80.0), l_shape) is True
    assert _inside((80.0, 80.0), l_shape) is False


def test_面に1つだけ入っていれば決まった面になる() -> None:
    words = [word(45.0, 45.0, "あいうえ")]
    assert _count([SQUARE], words, {"あいうえ"}) == (1, 0)


def test_面に2つ入っていれば決まらない面になる() -> None:
    words = [word(20.0, 20.0, "あいうえ"), word(60.0, 60.0, "かきくけ")]
    assert _count([SQUARE], words, {"あいうえ", "かきくけ"}) == (0, 1)


def test_表の升目に無い文字は数えない() -> None:
    words = [word(45.0, 45.0, "あいうえ")]
    assert _count([SQUARE], words, set()) == (0, 0)


def test_2文字以下は数えない() -> None:
    """**周1 の追記4 と同じ下限。**"""
    words = [word(45.0, 45.0, "あい")]
    assert _count([SQUARE], words, {"あい"}) == (0, 0)


def test_外にある文字は数えない() -> None:
    words = [word(500.0, 500.0, "あいうえ")]
    assert _count([SQUARE], words, {"あいうえ"}) == (0, 0)


def test_囮は形と大きさを変えない() -> None:
    moved = _shift(SQUARE, (1000.0, 800.0), random.Random(1))
    xs = [p[0] for p in moved]
    ys = [p[1] for p in moved]
    assert max(xs) - min(xs) == 100.0
    assert max(ys) - min(ys) == 100.0


def test_囮は紙の中に収まる() -> None:
    rng = random.Random(20260925)
    for _ in range(50):
        moved = _shift(SQUARE, (1000.0, 800.0), rng)
        assert min(p[0] for p in moved) >= 0.0
        assert max(p[0] for p in moved) <= 1000.0
        assert min(p[1] for p in moved) >= 0.0
        assert max(p[1] for p in moved) <= 800.0
