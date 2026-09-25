"""周31 の道具の試験。**合成の対象名だけを使う。実案件の室名も記号も書かない。**"""

from __future__ import annotations

from collections import Counter

from benchmarks.measure_rule_coverage import (
    DECOY_DRAWS,
    LINE3_TOP,
    coverage_of,
    kinds_of,
    scatter_kinds,
)
from estimating.quantities import QuantityItem


def _item(target: str) -> QuantityItem:
    return QuantityItem(
        target=target,
        value_range=(1.0, 1.0),
        unit="箇所",
        method_id="ごうせい",
    )


def test_種類は対象名の区切りより前だけを見る() -> None:
    counts = kinds_of([_item("あ::1"), _item("あ::2"), _item("い::1")])
    assert counts == Counter({"あ": 2, "い": 1})


def test_区切りが無い対象名は全体が種類になる() -> None:
    assert kinds_of([_item("あ")]) == Counter({"あ": 1})


def test_被覆は件数の割合であって種類の割合ではない() -> None:
    counts = Counter({"あ": 90, "い": 5, "う": 5})
    # **種類では 3 分の 1 だが、件数では 9 割。**ここを取り違えると順位が無意味になる。
    assert coverage_of(counts, ["あ"]) == 0.9


def test_空なら被覆はゼロ() -> None:
    assert coverage_of(Counter(), []) == 0.0


def test_偏っていれば上位が囮に勝つ() -> None:
    counts = Counter({f"k{i}": (1000 if i < LINE3_TOP else 1) for i in range(200)})
    top = [kind for kind, _ in counts.most_common(LINE3_TOP)]
    assert coverage_of(counts, top) - scatter_kinds(counts, LINE3_TOP, 1) > 0.2


def test_平らなら上位は囮に勝てない() -> None:
    """**囮が必ず負ける作りになっていないことの確かめ。**

    全部の種類が同じ件数なら、上位を選んでも無作為に選んでも同じになる。
    **勝てない場合があるから、勝ったときに意味がある。**
    """
    counts = Counter({f"k{i}": 10 for i in range(200)})
    top = [kind for kind, _ in counts.most_common(LINE3_TOP)]
    assert coverage_of(counts, top) == scatter_kinds(counts, LINE3_TOP, 1)


def test_種類が上位の数以下なら囮は本物と同じになる() -> None:
    counts = Counter({"あ": 3, "い": 1})
    assert scatter_kinds(counts, LINE3_TOP, 1) == 1.0


def test_囮は種を変えれば値が変わりうる() -> None:
    """**同じ値しか返さない囮なら、囮の役をしていない。**"""
    counts = Counter({f"k{i}": i + 1 for i in range(100)})
    seen = {scatter_kinds(counts, LINE3_TOP, seed) for seed in range(8)}
    assert len(seen) > 1


def test_囮は決めた回数だけ引く() -> None:
    assert DECOY_DRAWS == 10
