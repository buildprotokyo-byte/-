"""周7 の測定の道具のテスト(`benchmarks/measure_rule_placed_reading.py`)。

**合成データだけで確かめる。**実図面は使わない。文字の読み取りの部品は呼ばない。
"""

from __future__ import annotations

import pymupdf

from benchmarks.measure_rule_placed_reading import (
    _covers,
    _levels,
    cells,
    rule_segments,
)


def _table_page(rows: int, columns: int, cell: float = 30.0) -> pymupdf.Page:
    """罫線だけで表を描いたページ。**文字は 1 つも置かない。**"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    shape = page.new_shape()
    left, top = 40.0, 40.0
    for row in range(rows + 1):
        y = top + row * cell
        shape.draw_line(
            pymupdf.Point(left, y), pymupdf.Point(left + columns * cell, y)
        )
    for column in range(columns + 1):
        x = left + column * cell
        shape.draw_line(pymupdf.Point(x, top), pymupdf.Point(x, top + rows * cell))
    shape.finish(width=0.8, color=(0, 0, 0))
    shape.commit()
    return page


def test_文字が無くても罫線から升目が取れる() -> None:
    """**これが周7 の出発点。**既にある表の部品は文字を手がかりにするので 0 個になる。"""
    page = _table_page(rows=3, columns=4)
    assert page.get_text().strip() == ""
    assert len(cells(page)) == 12


def test_罫線は線分のまま集め_同じ座標は1本にまとめる() -> None:
    """線分は重複して取れることがあるので、**まとめた本数**で数える。"""
    horizontal, vertical = rule_segments(_table_page(rows=2, columns=2))
    assert len(_levels(horizontal)) == 3
    assert len(_levels(vertical)) == 3
    assert all(start < end for _, start, end in horizontal + vertical)


def test_辺が途切れている矩形は升目にしない() -> None:
    """**4 辺に墨が続いていることが升目の定義。**片側が無ければ升目ではない。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(40, 40), pymupdf.Point(140, 40))
    shape.draw_line(pymupdf.Point(40, 100), pymupdf.Point(140, 100))
    shape.draw_line(pymupdf.Point(40, 40), pymupdf.Point(40, 100))
    # 右の縦線を引かない
    shape.finish(width=0.8, color=(0, 0, 0))
    shape.commit()
    assert cells(page) == []


def test_離れた線どうしを升目にしない() -> None:
    """紙の端と端にある線が、組み合わさって 1 つの升目になってはいけない。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(10, 20), pymupdf.Point(60, 20))
    shape.draw_line(pymupdf.Point(300, 250), pymupdf.Point(360, 250))
    shape.draw_line(pymupdf.Point(20, 10), pymupdf.Point(20, 60))
    shape.draw_line(pymupdf.Point(350, 200), pymupdf.Point(350, 260))
    shape.finish(width=0.8, color=(0, 0, 0))
    shape.commit()
    assert cells(page) == []


def test_覆っている範囲の判定() -> None:
    assert _covers([(0.0, 10.0)], 2.0, 8.0) is True
    assert _covers([(0.0, 4.0), (4.5, 10.0)], 0.0, 10.0) is True
    assert _covers([(0.0, 3.0), (7.0, 10.0)], 0.0, 10.0) is False
    assert _covers([], 0.0, 10.0) is False


def test_升目は表の外へはみ出さない() -> None:
    page = _table_page(rows=2, columns=2, cell=40.0)
    for x0, y0, x1, y1 in cells(page):
        assert 39.0 <= x0 and x1 <= 121.0
        assert 39.0 <= y0 and y1 <= 121.0
