"""周23 の道具(`benchmarks/measure_horizontal_chain.py`)の試験。

**実図面は使わない。**手で置いた寸法で、数え方だけを固定する。
"""

from __future__ import annotations

from benchmarks.measure_horizontal_chain import (
    LINE1_MIN,
    LINE2_MIN,
    LINE3_MIN,
    check_definition,
    gaps_on_the_same_line,
    halves,
    same_line,
    scatter_levels,
    with_a_partner_at_the_same_level,
    with_a_partner_on_the_same_line,
)


def test_向きが違えば同じ寸法線とは数えない() -> None:
    first = ("横", (0.0, 100.0), (50.0, 100.0))
    second = ("縦", (0.0, 100.0), (0.0, 150.0))
    assert same_line(first, second) is False


def test_0_5pt_より離れていたら同じ直線とは数えない() -> None:
    first = ("横", (0.0, 100.0), (50.0, 100.0))
    assert same_line(first, ("横", (80.0, 100.4), (130.0, 100.4))) is True
    assert same_line(first, ("横", (80.0, 101.0), (130.0, 101.0))) is False


def test_同じ高さは5ptまで許す() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 104.0), (250.0, 104.0)),
        ("横", (0.0, 400.0), (50.0, 400.0)),
    ]
    assert with_a_partner_at_the_same_level(readings) == 2


def test_相手のいない読みは数えない() -> None:
    readings = [("横", (0.0, 100.0), (50.0, 100.0))]
    assert with_a_partner_on_the_same_line(readings) == 0
    assert with_a_partner_at_the_same_level(readings) == 0


def test_隣り合う組と間が空いた組を分ける() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (50.0, 100.0), (90.0, 100.0)),
        ("横", (300.0, 100.0), (350.0, 100.0)),
    ]
    got = gaps_on_the_same_line(readings)
    assert got["隣り合う(隙間 5pt 未満)"] == 1
    assert got["間が空いている"] == 2


def test_別の直線どうしは並びに数えない() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (0.0, 400.0), (50.0, 400.0)),
    ]
    assert gaps_on_the_same_line(readings) == {
        "隣り合う(隙間 5pt 未満)": 0,
        "間が空いている": 0,
    }


def test_上下の分かれ方() -> None:
    readings = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (0.0, 700.0), (50.0, 700.0)),
    ]
    assert halves(readings, 800.0) == {"上半分": 1, "下半分": 1}


def test_囮は高さだけを動かし_長さとxは変えない() -> None:
    readings = [("横", (10.0, 100.0), (60.0, 100.0))]
    (orientation, start, end), = scatter_levels(readings, 800.0, 7)
    assert orientation == "横"
    assert (start[0], end[0]) == (10.0, 60.0)
    assert end[1] - start[1] == 0.0


def test_囮は種が同じなら何度でも同じ() -> None:
    readings = [("横", (10.0, 100.0), (60.0, 100.0))]
    assert scatter_levels(readings, 800.0, 7) == scatter_levels(readings, 800.0, 7)


def test_合成の確かめ() -> None:
    got = check_definition()
    assert got["同じ直線に乗る2件を拾う"] == 2
    assert got["3pt ずれていても同じ高さと数える"] == 2
    assert got["3pt ずれていたら同じ直線とは数えない"] == 0
    assert got["上下の分かれ方"] == {"上半分": 2, "下半分": 1}


def test_合格の線は基準のまま() -> None:
    assert (LINE1_MIN, LINE2_MIN, LINE3_MIN) == (10, 10, 5)
