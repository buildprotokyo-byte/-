"""周24 の道具(`benchmarks/measure_gap_numbers.py`)の試験。

**実図面は使わない。**手で置いた寸法と点で、数え方だけを固定する。
"""

from __future__ import annotations

from benchmarks.measure_gap_numbers import (
    BAND_PT,
    GAP_MIN_PT,
    check_definition,
    count_in_gaps,
    gaps,
    inside,
    scatter_points,
)


def test_隣り合う読みは空きにしない() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (52.0, 100.0), (100.0, 100.0)),
    ]
    assert gaps(readings) == []


def test_別の直線どうしは空きにしない() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 400.0), (250.0, 400.0)),
    ]
    assert gaps(readings) == []


def test_空きの矩形は内側の端から端まで() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 100.0), (250.0, 100.0)),
    ]
    (box,) = gaps(readings)
    assert box == (50.0, 100.0 - BAND_PT, 200.0, 100.0 + BAND_PT)


def test_左右の順番が逆でも同じ空きになる() -> None:
    first = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 100.0), (250.0, 100.0)),
    ]
    second = list(reversed(first))
    assert gaps(first) == gaps(second)


def test_帯の外の点は入らない() -> None:
    box = (50.0, 88.0, 200.0, 112.0)
    assert inside((100.0, 100.0), box) is True
    assert inside((100.0, 113.0), box) is False


def test_2個入った空きはちょうど1個に数えない() -> None:
    boxes = [(0.0, 0.0, 100.0, 100.0)]
    got = count_in_gaps(boxes, [(10.0, 10.0), (20.0, 20.0)])
    assert got["落とし物が1個以上入った空き"] == 1
    assert got["ちょうど1個だけ入った空き"] == 0


def test_空きが無ければ全部0() -> None:
    assert count_in_gaps([], [(10.0, 10.0)]) == {
        "空き": 0,
        "落とし物が1個以上入った空き": 0,
        "ちょうど1個だけ入った空き": 0,
    }


def test_囮は個数を変えない() -> None:
    points = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    got = scatter_points(points, 800.0, 600.0, 20260925)
    assert len(got) == len(points)
    assert got != points
    assert all(0.0 <= x <= 800.0 and 0.0 <= y <= 600.0 for x, y in got)


def test_囮は種が同じなら何度でも同じ() -> None:
    points = [(1.0, 2.0), (3.0, 4.0)]
    assert scatter_points(points, 800.0, 600.0, 7) == scatter_points(
        points, 800.0, 600.0, 7
    )


def test_合成の確かめ() -> None:
    got = check_definition()
    assert got["空き"] == 1
    assert got["中の点を拾う"]["ちょうど1個だけ入った空き"] == 1
    assert got["帯の外の点は拾わない"]["落とし物が1個以上入った空き"] == 0
    assert got["2個入ったらちょうど1個には数えない"]["ちょうど1個だけ入った空き"] == 0


def test_しきいは基準のまま() -> None:
    assert (GAP_MIN_PT, BAND_PT) == (5.0, 12.0)
