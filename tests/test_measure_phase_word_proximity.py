"""周33 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmarks.measure_phase_word_proximity import (
    DISTANCES_MM,
    MM_PER_POINT,
    _as_points,
    decoy_shares,
    distances_for,
    hits_within,
    nearest_mm,
    page_of,
    points_of,
    share_within,
)


def quantity(provenance: dict) -> SimpleNamespace:
    return SimpleNamespace(provenance=provenance)


def test_2つ組は1つの点になる() -> None:
    assert _as_points([1.0, 2.0]) == [(1.0, 2.0)]


def test_点の並びはそのまま点になる() -> None:
    assert _as_points([[1, 2], [3, 4]]) == [(1.0, 2.0), (3.0, 4.0)]


def test_4つ組は点として使わない() -> None:
    # `rect_pt` は矩形なので、点の並びに混ぜると場所がずれる。
    assert _as_points([1, 2, 3, 4]) == []


def test_開き戸の中心をたどる() -> None:
    item = quantity({"page_number": 3, "arcs": [{"center_pt": [10.0, 20.0]}]})
    assert points_of(item) == [(10.0, 20.0)]
    assert page_of(item) == 3


def test_室の輪郭をたどる() -> None:
    item = quantity({"page_number": 1, "polygon_pt": [[0, 0], [0, 5]]})
    assert points_of(item) == [(0.0, 0.0), (0.0, 5.0)]


def test_記号の位置をたどる() -> None:
    item = quantity({"page_number": 2, "positions_pt": [[7, 8]]})
    assert points_of(item) == [(7.0, 8.0)]


def test_矩形だけの根拠からは点が取れない() -> None:
    item = quantity({"page_number": 2, "arcs": [{"rect_pt": [0, 0, 1, 1]}]})
    assert points_of(item) == []


def test_座標が無ければ空になる() -> None:
    assert points_of(quantity({"page_number": 1})) == []
    assert page_of(quantity({})) is None


def test_距離は紙の上のミリで返る() -> None:
    got = nearest_mm([(0.0, 0.0)], [(72.0, 0.0)])
    assert got == pytest.approx(72.0 * MM_PER_POINT)
    assert got == pytest.approx(25.4)


def test_いちばん近い語までの距離を返す() -> None:
    got = nearest_mm([(0.0, 0.0)], [(720.0, 0.0), (72.0, 0.0)])
    assert got == pytest.approx(25.4)


def test_語が無ければ距離は出ない() -> None:
    assert nearest_mm([(0.0, 0.0)], []) is None
    assert nearest_mm([], [(0.0, 0.0)]) is None


def test_届かなかったものも分母に入る() -> None:
    # 分母から外すと、届かないページが多いほど割合が上がってしまう。
    assert share_within([5.0, None], limit_mm=10.0, total=2) == pytest.approx(0.5)


def test_分母がゼロなら割合はゼロ() -> None:
    assert share_within([], limit_mm=10.0, total=0) == 0.0


def test_当たった番号の集合が返る() -> None:
    assert hits_within([5.0, 50.0, None], limit_mm=10.0) == {0}
    assert hits_within([5.0, 50.0, None], limit_mm=100.0) == {0, 1}


def test_狭い集合は広い集合に含まれる() -> None:
    values = [5.0, 30.0, None, 8.0]
    assert hits_within(values, 10.0) <= hits_within(values, 50.0)


def test_近さの3段は狭いほうから並んでいる() -> None:
    assert list(DISTANCES_MM) == sorted(DISTANCES_MM)


def test_ページごとに語を引き当てる() -> None:
    items = [quantity({"page_number": 1, "positions_pt": [[0, 0]]})]
    got = distances_for(items, {1: [(72.0, 0.0)]})
    assert got[0] == pytest.approx(25.4)
    # 別のページの語は使わない。
    assert distances_for(items, {2: [(72.0, 0.0)]}) == [None]


def test_囮は同じ種なら同じ値になる() -> None:
    items = [quantity({"page_number": 1, "positions_pt": [[0, 0]]})]
    others = {1: [(x * 10.0, 0.0) for x in range(50)]}
    wanted = {1: 5}
    first = decoy_shares(items, others, wanted, seed=20260925)
    second = decoy_shares(items, others, wanted, seed=20260925)
    assert first == second


def test_囮は本物に勝つことがある() -> None:
    # 位相以外の語が物のすぐそばに詰まっていれば、囮のほうが近い。
    items = [quantity({"page_number": 1, "positions_pt": [[0, 0]]})]
    others = {1: [(1.0, 0.0)] * 20}
    got = decoy_shares(items, others, {1: 3}, seed=20260925)
    assert got[DISTANCES_MM[0]] == pytest.approx(1.0)


def test_引く語が無ければ囮はゼロになる() -> None:
    items = [quantity({"page_number": 1, "positions_pt": [[0, 0]]})]
    got = decoy_shares(items, {1: []}, {1: 3}, seed=20260925)
    assert got[DISTANCES_MM[0]] == 0.0
