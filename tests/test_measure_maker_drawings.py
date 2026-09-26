"""周6 の測定の道具のテスト(`benchmarks/measure_maker_drawings.py`)。

**合成データだけで確かめる。**実図面は使わない。
文字の読み取り(rapidocr)はこの環境の外では入っていないので、**読む部分は呼ばない。**
"""

from __future__ import annotations

import random

import numpy as np
import pymupdf
import pytest

from benchmarks.measure_maker_drawings import (
    INK_LEVEL,
    TILE_COLUMNS,
    TILE_ROWS,
    ink_fraction,
    render,
    render_shifted,
    tiles,
)


def _page_with_lines(count: int) -> pymupdf.Page:
    """線を count 本引いただけのページを作る。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    shape = page.new_shape()
    for index in range(count):
        y = 10.0 + index * 5.0
        shape.draw_line(pymupdf.Point(20, y), pymupdf.Point(120, y))
    shape.finish(width=1.0, color=(0, 0, 0))
    shape.commit()
    return page


def test_墨が無い区画の割合は0になる() -> None:
    blank = np.full((10, 10), 255, dtype=np.uint8)
    assert ink_fraction(blank) == 0.0


def test_墨がある区画の割合は0より大きい() -> None:
    tile = np.full((10, 10), 255, dtype=np.uint8)
    tile[0, 0] = INK_LEVEL - 1
    assert ink_fraction(tile) == pytest.approx(0.01)


def test_区画の数は決めた格子のとおりになる() -> None:
    image = np.full((120, 180), 255, dtype=np.uint8)
    assert len(tiles(image)) == TILE_COLUMNS * TILE_ROWS


def test_区画は画像を余さず覆う() -> None:
    image = np.arange(120 * 180, dtype=np.uint8).reshape(120, 180)
    total = sum(tile.size for _, _, tile in tiles(image))
    assert total == image.size


def test_ページを画像にすると2次元の配列が返る() -> None:
    image = render(_page_with_lines(3), dpi=72)
    assert image.ndim == 2
    assert image.shape[0] > 0 and image.shape[1] > 0


def test_囮の紙は図形の数を変えない() -> None:
    page = _page_with_lines(8)
    before = len(page.get_drawings())
    shifted = render_shifted(page, random.Random(20260925))
    assert len(shifted.get_drawings()) == before


def test_囮の紙は墨の量をおおむね保つ() -> None:
    """位置を動かすだけなので、墨の画素の数は大きく変わらない。"""
    page = _page_with_lines(20)
    real = render(page, dpi=72)
    fake = render(render_shifted(page, random.Random(20260925)), dpi=72)
    real_ink = int((real < INK_LEVEL).sum())
    fake_ink = int((fake < INK_LEVEL).sum())
    assert real_ink > 0
    assert 0.5 <= fake_ink / real_ink <= 2.0


def test_囮の紙は位置を動かしている() -> None:
    """**同じ画像のままなら囮にならない。**必ず違う絵になることを固定する。"""
    page = _page_with_lines(20)
    real = render(page, dpi=72)
    fake = render(render_shifted(page, random.Random(20260925)), dpi=72)
    assert not np.array_equal(real, fake)


def test_囮は種が同じなら同じ紙になる() -> None:
    page = _page_with_lines(12)
    one = render(render_shifted(page, random.Random(7)), dpi=72)
    two = render(render_shifted(page, random.Random(7)), dpi=72)
    assert np.array_equal(one, two)
