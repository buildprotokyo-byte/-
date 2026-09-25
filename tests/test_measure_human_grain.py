"""周35 の測定道具のテスト。**合成のデータだけを使う。実図面は使わない。**"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmarks.measure_human_grain import (
    DECOY_GRAIN,
    NEAR_MM,
    PHASE_GROUPS,
    cover_of,
    groups_near,
    mixed_share,
    units_of,
)
from benchmarks.measure_phase_printed import PHASE_WORDS
from benchmarks.measure_phase_word_proximity import MM_PER_POINT, page_of


def quantity(page: int, x: float, y: float, kind: str = "あ") -> SimpleNamespace:
    return SimpleNamespace(
        provenance={"page_number": page, "positions_pt": [[x, y]]},
        kind=kind,
        target=f"{kind}::{x}",
    )


def test_3つの組は周32の12語をちょうど分けている() -> None:
    # 語を足したり落としたりすると、周32 と比べられなくなる。
    flat = [word for words in PHASE_GROUPS.values() for word in words]
    assert sorted(flat) == sorted(PHASE_WORDS)
    assert len(flat) == len(set(flat))


def test_囮の粒度には囮と分かる名前が付いている() -> None:
    assert "囮" in DECOY_GRAIN


def test_近くの組だけを拾う() -> None:
    near = NEAR_MM / MM_PER_POINT
    positions = {1: {"現況の側": [(0.0, 0.0)], "計画の側": [(near * 10, 0.0)]}}
    assert groups_near(quantity(1, 0.0, 0.0), positions) == {"現況の側"}


def test_別のページの語は拾わない() -> None:
    positions = {2: {"現況の側": [(0.0, 0.0)]}}
    assert groups_near(quantity(1, 0.0, 0.0), positions) == set()


def test_2つの組が近ければ両方拾う() -> None:
    positions = {1: {"現況の側": [(0.0, 0.0)], "計画の側": [(1.0, 0.0)]}}
    assert groups_near(quantity(1, 0.0, 0.0), positions) == {"現況の側", "計画の側"}


def test_粒度ごとにまとまる() -> None:
    items = [quantity(1, 0, 0, "あ"), quantity(1, 1, 0, "あ"), quantity(2, 0, 0, "い")]
    assert sorted(len(v) for v in units_of(items, page_of).values()) == [1, 2]
    assert sorted(len(v) for v in units_of(items, lambda i: i.kind).values()) == [1, 2]


def test_鍵が付かない数量は覆えない() -> None:
    units = {None: [1, 2], "あ": [3]}
    assert cover_of(units, 3) == pytest.approx(1 / 3)


def test_数量がゼロなら覆える割合もゼロ() -> None:
    assert cover_of({}, 0) == 0.0


def test_2組以上が出た単位は決まらないと数える() -> None:
    positions = {1: {"現況の側": [(0.0, 0.0)], "計画の側": [(1.0, 0.0)]}}
    share, mixed, silent = mixed_share({"a": [quantity(1, 0, 0)]}, positions)
    assert (share, mixed, silent) == (1.0, 1, 0)


def test_1組だけなら決まったと数える() -> None:
    positions = {1: {"現況の側": [(0.0, 0.0)]}}
    share, mixed, silent = mixed_share({"a": [quantity(1, 0, 0)]}, positions)
    assert (share, mixed, silent) == (0.0, 0, 0)


def test_語が出ない単位は分母から外す() -> None:
    # 決めようがないのであって、混ざっているのではない。
    positions = {1: {"現況の側": [(0.0, 0.0)]}, 2: {}}
    units = {"a": [quantity(1, 0, 0)], "b": [quantity(2, 0, 0)]}
    share, mixed, silent = mixed_share(units, positions)
    assert (share, mixed, silent) == (0.0, 0, 1)


def test_語がどこにも出なければ割合はゼロになる() -> None:
    share, mixed, silent = mixed_share({"a": [quantity(1, 0, 0)]}, {1: {}})
    assert (share, mixed, silent) == (0.0, 0, 1)


def test_囮の粒度はすべてを1つにまとめる() -> None:
    items = [quantity(1, 0, 0, "あ"), quantity(2, 0, 0, "い")]
    assert len(units_of(items, lambda _i: "案件")) == 1
