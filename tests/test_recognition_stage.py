"""1 段目(認識)の測り方の試験。**合成の図形だけを使う。**実図面は読まない。"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.measure_recognition_stage import (
    choose_face,
    frame_centre,
    item_rects,
    range_rect,
    untouched,
)


class _Face:
    """試験用の面。`RoomOutline` のうち、選ぶのに使う所だけを持つ。"""

    def __init__(self, bbox, area=10.0):
        x0, y0, x1, y1 = bbox
        self.polygon_pt = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        self.area_sqm = area


class TestFrameCentre:
    def test_表題欄を外した図枠の中心を返す(self):
        page = pymupdf.open().new_page(width=1000, height=800)
        assert frame_centre(page, title_block_x=100.0) == pytest.approx((550.0, 400.0))

    def test_表題欄が無ければ紙の中心(self):
        page = pymupdf.open().new_page(width=1000, height=800)
        assert frame_centre(page, title_block_x=0.0) == pytest.approx((500.0, 400.0))


class TestChooseFace:
    def test_中心にいちばん近い面を選ぶ(self):
        near = _Face((450, 350, 550, 450))
        far = _Face((0, 0, 100, 100))
        assert choose_face([far, near], (500.0, 400.0)) is near

    def test_ほかの面を内側に含む面は選ばない(self):
        """**外形・通り芯の枠は、ほかの室を含むので室ではない。**"""
        outer = _Face((0, 0, 1000, 800), area=400.0)
        inner = _Face((100, 100, 300, 300))
        assert choose_face([outer, inner], (500.0, 400.0)) is inner

    def test_含む面しか無ければ何も選ばない(self):
        outer = _Face((0, 0, 1000, 800))
        inner = _Face((100, 100, 300, 300))
        assert choose_face([outer], (500.0, 400.0)) is outer
        assert choose_face([], (500.0, 400.0)) is None

    def test_同じ大きさで重ならない面は互いに含まない(self):
        left = _Face((0, 0, 100, 100))
        right = _Face((200, 0, 300, 100))
        assert choose_face([left, right], (250.0, 50.0)) is right


class TestRangeRect:
    def test_外接矩形に余白を足す(self):
        rect = range_rect(_Face((100, 200, 300, 400)), padding=20.0)
        assert (rect.x0, rect.y0, rect.x1, rect.y1) == (80.0, 180.0, 320.0, 420.0)


class TestItemRects:
    def test_描画命令を1つずつ数える(self):
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(90, 10))
        page.draw_rect(pymupdf.Rect(20, 20, 60, 60))
        page.draw_circle(pymupdf.Point(100, 100), 20)
        items = item_rects(page)
        kinds = {kind for _, kind, _ in items}
        assert len(items) >= 3
        assert "l" in kinds
        assert "c" in kinds

    def test_番号は命令ごとに違う(self):
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(90, 10))
        page.draw_line(pymupdf.Point(10, 20), pymupdf.Point(90, 20))
        keys = [key for key, _, _ in item_rects(page)]
        assert len(keys) == len(set(keys))


class TestUntouched:
    def test_どの経路も触れなかった命令を返す(self):
        items = [((0, 0), "l", pymupdf.Rect(0, 0, 10, 10)),
                 ((0, 1), "c", pymupdf.Rect(50, 50, 60, 60))]
        touched = {"面": {(0, 0)}}
        assert untouched(items, touched) == [((0, 1), "c")]

    def test_全部触れられていれば空(self):
        items = [((0, 0), "l", pymupdf.Rect(0, 0, 10, 10))]
        assert untouched(items, {"面": {(0, 0)}, "表": {(0, 0)}}) == []
