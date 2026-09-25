"""周22 の道具(`benchmarks/measure_dimension_grid.py`)の試験。

**実図面は使わない。**手で置いた寸法と室名で、数え方だけを固定する。
"""

from __future__ import annotations

from benchmarks.measure_dimension_grid import (
    LINE4_MIN,
    chain_members,
    check_definition,
    count_in_grid,
    grid_lines,
    scatter_grid,
)


def test_連なっていない寸法は格子に使わない() -> None:
    readings = [
        ("横", (0.0, 0.0), (100.0, 0.0)),
        ("横", (100.0, 0.0), (200.0, 0.0)),
        ("横", (500.0, 500.0), (600.0, 500.0)),
    ]
    members = chain_members(readings)
    assert len(members) == 2
    assert ("横", (500.0, 500.0), (600.0, 500.0)) not in members


def test_向きが違う寸法は同じ連なりにしない() -> None:
    """端点が同じでも、向きが違えば連なりにしない。"""
    readings = [
        ("横", (0.0, 0.0), (100.0, 0.0)),
        ("縦", (0.0, 0.0), (0.0, 100.0)),
    ]
    assert chain_members(readings) == []


def test_横の寸法は縦の格子線に_縦の寸法は横の格子線になる() -> None:
    readings = [
        ("横", (0.0, 0.0), (100.0, 0.0)),
        ("横", (100.0, 0.0), (200.0, 0.0)),
    ]
    xs, ys = grid_lines(chain_members(readings))
    assert xs == [0.0, 100.0, 200.0]
    assert ys == []


def test_斜めの寸法は格子線にしない() -> None:
    readings = [
        ("斜め", (0.0, 0.0), (100.0, 100.0)),
        ("斜め", (100.0, 100.0), (200.0, 200.0)),
    ]
    assert grid_lines(chain_members(readings)) == ([], [])


def test_近すぎる格子線は1本にまとめる() -> None:
    readings = [
        ("横", (0.0, 0.0), (100.0, 0.0)),
        ("横", (102.0, 0.0), (200.0, 0.0)),
    ]
    xs, _ = grid_lines(chain_members(readings))
    assert xs == [0.0, 100.0, 200.0]


def test_室名が2個入った升目は数えない() -> None:
    xs, ys = [0.0, 100.0], [0.0, 100.0]
    counted = count_in_grid(xs, ys, [("室A", (10.0, 10.0)), ("室B", (20.0, 20.0))])
    assert counted == {"升目": 1, "室名が1個": 0, "格子の外の室名": 0}


def test_格子の外の室名は別に数える() -> None:
    xs, ys = [0.0, 100.0], [0.0, 100.0]
    counted = count_in_grid(xs, ys, [("室A", (10.0, 10.0)), ("室B", (500.0, 500.0))])
    assert counted == {"升目": 1, "室名が1個": 1, "格子の外の室名": 1}


def test_格子線が1本以下なら升目は0() -> None:
    assert count_in_grid([5.0], [0.0, 100.0], [("室A", (5.0, 5.0))]) == {
        "升目": 0,
        "室名が1個": 0,
        "格子の外の室名": 1,
    }


def test_囮は格子線の本数を保つ() -> None:
    """**囮′ は本数を変えない。**変えるのは位置だけ。"""
    xs, ys = [0.0, 100.0, 200.0], [0.0, 50.0]
    decoy_xs, decoy_ys = scatter_grid(xs, ys, 800.0, 800.0, 20260925)
    assert len(decoy_xs) == len(xs)
    assert len(decoy_ys) == len(ys)
    assert decoy_xs != xs


def test_囮は種が同じなら何度でも同じ() -> None:
    xs, ys = [0.0, 100.0, 200.0], [0.0, 50.0, 100.0]
    first = scatter_grid(xs, ys, 800.0, 800.0, 7)
    second = scatter_grid(xs, ys, 800.0, 800.0, 7)
    assert first == second


def test_合成の確かめ() -> None:
    """**実図面に当てる前の確かめ**(周12 の教訓)がそのまま通ること。"""
    got = check_definition()
    assert got["1本きりの寸法を外した"] is True
    assert got["格子線"] == [3, 3]
    assert got["升目と室名"]["升目"] == 4
    # 室D と 室E は同じ升目に入るので、ちょうど1個は 3 個。
    assert got["升目と室名"]["室名が1個"] == 3
    assert got["囮_格子線"] == [3, 3]


def test_合格の線は基準のまま() -> None:
    assert LINE4_MIN == 3
