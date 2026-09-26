"""周3 の見分けのテスト。**合成データだけ。**実図面も実際の正解も使わない。"""

from __future__ import annotations

from benchmarks.measure_page_kind import (
    KIND_DRAWING,
    KIND_IMAGE,
    KIND_TABLE,
    _shuffled,
    decide,
)


def _feature(**kwargs: object) -> dict:
    base = {"文字": 0, "図形": 0, "画像": 0, "画像割合": 0.0, "升目": 0, "横長": True}
    base.update(kwargs)
    return base


def test_画像が多ければ画像が主() -> None:
    assert decide(_feature(画像=25, 図形=9999, 升目=9999)) == KIND_IMAGE


def test_図形が少なく升目が多ければ表() -> None:
    assert decide(_feature(図形=10, 升目=400)) == KIND_TABLE


def test_図形が多ければ図面() -> None:
    assert decide(_feature(図形=5000, 升目=20)) == KIND_DRAWING


def test_升目が多くても図形が多ければ図面() -> None:
    """**順番が効いている。**表の見分けは図形が少ないときだけ。"""
    assert decide(_feature(図形=5000, 升目=400)) == KIND_DRAWING


def test_手がかりが乏しいときは表と答える() -> None:
    assert decide(_feature(図形=10, 升目=40)) == KIND_TABLE


def test_囮は手がかりごとに別々に混ぜる() -> None:
    """**紙1枚ぶんの組み合わせが壊れることを固定する。**"""
    rows = [
        _feature(文字=index, 図形=index * 10, 画像=index, 升目=index * 2)
        for index in range(10)
    ]
    fake = _shuffled(rows, seed=20260925)
    assert len(fake) == len(rows)
    assert sorted(row["図形"] for row in fake) == sorted(row["図形"] for row in rows)
    assert [row["図形"] for row in fake] != [row["図形"] for row in rows]


def test_囮は種が同じなら同じ並びになる() -> None:
    rows = [_feature(文字=index, 図形=index * 3) for index in range(8)]
    assert _shuffled(rows, 7) == _shuffled(rows, 7)
