"""周11 の測定の道具のテスト。**合成のページだけを使う。実図面は使わない。**"""

from __future__ import annotations

import pymupdf
import pytest

from axes.image_axis.pdf_vector_symbols import DrawingScale
from benchmarks.measure_opening_width import (
    DOOR_NEAR_MM,
    WIDTHS_MM,
    false_walls,
)


@pytest.fixture()
def 部屋が2つある紙(tmp_path):
    """真ん中に 2,000mm の切れ目がある壁。**建具の円弧は描かない。**"""
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    shape = page.new_shape()
    for rect in ((50, 50, 350, 350),):
        shape.draw_rect(pymupdf.Rect(*rect))
    shape.draw_line(pymupdf.Point(200, 50), pymupdf.Point(200, 150))
    shape.draw_line(pymupdf.Point(200, 250), pymupdf.Point(200, 350))
    shape.finish(width=1)
    shape.commit()
    path = tmp_path / "plan.pdf"
    document.save(path)
    document.close()
    return path


def test_今の幅が測る幅の先頭にある() -> None:
    """**1,200mm が今の既定値**で、そこを基準に増減を見る。"""
    assert WIDTHS_MM[0] == 1200.0
    assert list(WIDTHS_MM) == sorted(WIDTHS_MM)


def test_建具が無ければ仮の辺は全部嘘の壁になる(部屋が2つある紙) -> None:
    """円弧を 1 つも描いていない紙では、閉じた仮の辺は全部嘘に数える。"""
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    result = false_walls(部屋が2つある紙, 0, scale, width_mm=3000.0)
    assert result["建具の円弧"] == 0
    assert result["嘘の壁"] == result["仮の辺"]


def test_つなげない幅では仮の辺が出ない(部屋が2つある紙) -> None:
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    result = false_walls(部屋が2つある紙, 0, scale, width_mm=1.0)
    assert result["仮の辺"] == 0
    assert result["嘘の壁"] == 0


def test_嘘の壁は仮の辺を超えない(部屋が2つある紙) -> None:
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    for width_mm in WIDTHS_MM:
        result = false_walls(部屋が2つある紙, 0, scale, width_mm)
        assert 0 <= result["嘘の壁"] <= result["仮の辺"]


def test_近さを広げれば嘘の壁は増えない(部屋が2つある紙) -> None:
    """**建具が近いとみなす距離を広げて、嘘が増えることはない。**"""
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    narrow = false_walls(部屋が2つある紙, 0, scale, 3000.0, near_mm=DOOR_NEAR_MM)
    wide = false_walls(部屋が2つある紙, 0, scale, 3000.0, near_mm=DOOR_NEAR_MM * 10)
    assert wide["嘘の壁"] <= narrow["嘘の壁"]


def test_線が1本も無い紙では何も出ない(tmp_path) -> None:
    document = pymupdf.open()
    document.new_page(width=400, height=400)
    path = tmp_path / "blank.pdf"
    document.save(path)
    document.close()
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    result = false_walls(path, 0, scale, width_mm=3000.0)
    assert result["仮の辺"] == 0
    assert result["嘘の壁"] == 0
    assert result["建具の円弧"] == 0
