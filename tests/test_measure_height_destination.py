"""周5 の測定の道具のテスト。**合成のページだけを使う。実図面は使わない。**"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.measure_height_destination import (
    CEILING_NOTE,
    ceiling_notes,
    land,
    scatter,
    settled,
)


def _page_with(texts: list[tuple[str, float, float]]) -> pymupdf.Page:
    document = pymupdf.open()
    page = document.new_page(width=400, height=300)
    for text, x, y in texts:
        page.insert_text((x, y), text, fontsize=8)
    return page


def test_天井高の注記だけを拾う() -> None:
    page = _page_with([("CH=2400", 50, 50), ("FL+1000", 50, 80), ("洋室", 50, 110)])
    found = ceiling_notes(page)
    assert len(found) == 1


def test_全角の等号と数字も拾う() -> None:
    assert CEILING_NOTE.search("CH＝２４００") is not None
    assert CEILING_NOTE.search("CH = 2,400") is not None


def test_取付高さは天井高として拾わない() -> None:
    """``FL+1000`` はスイッチの取付高さで、室の天井高ではない。"""
    page = _page_with([("FL+1000", 50, 50), ("FL±0", 50, 80)])
    assert ceiling_notes(page) == []


def test_囮は数を変えず位置だけを変える() -> None:
    points = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
    moved = scatter(points, 400.0, 300.0, seed=20260925)
    assert len(moved) == len(points)
    assert moved != points


def test_囮は紙の中に収まる() -> None:
    points = [(10.0, 10.0)] * 50
    for x, y in scatter(points, 400.0, 300.0, seed=20260925):
        assert 0.0 <= x <= 400.0
        assert 0.0 <= y <= 300.0


def test_囮は種が同じなら同じ紙になる() -> None:
    points = [(10.0, 10.0), (20.0, 20.0)]
    assert scatter(points, 400.0, 300.0, 7) == scatter(points, 400.0, 300.0, 7)


def test_面の内側に入った点だけを数える() -> None:
    square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    inside, per_face = land([(5.0, 5.0), (50.0, 50.0)], [square])
    assert inside == 1
    assert per_face == {0: 1}


def test_2つ入った面は行き先が決まらない() -> None:
    square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    _, per_face = land([(3.0, 3.0), (7.0, 7.0)], [square])
    assert settled(per_face) == 0


def test_ちょうど1つ入った面だけを数える() -> None:
    left = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    right = ((20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0))
    _, per_face = land([(5.0, 5.0), (25.0, 3.0), (25.0, 7.0)], [left, right])
    assert settled(per_face) == 1


def test_面が無ければ何も入らない() -> None:
    inside, per_face = land([(5.0, 5.0)], [])
    assert inside == 0
    assert per_face == {}


@pytest.mark.parametrize("text", ["CH", "CH=", "天井高"])
def test_数字が付かない語は注記にしない(text: str) -> None:
    assert CEILING_NOTE.search(text) is None
