"""ページの構造を数える道具の試験。**合成データだけ**を使う。

基準は `docs/k30_page_structure_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.measure_k30_page_structure import (
    KINDS,
    Segment,
    classify,
    largest_closed_rect,
    merge,
    segments,
    spans,
    title_block_rect,
)

PAPER = pymupdf.Rect(0, 0, 1191, 842)


def _page(doc, *, rotation: int = 0):
    page = doc.new_page(width=PAPER.width, height=PAPER.height)
    if rotation:
        page.set_rotation(rotation)
    return page


def _draw_box(page, rect: pymupdf.Rect, width: float = 1.0) -> None:
    page.draw_rect(rect, color=(0, 0, 0), width=width)


class Test線をまとめる:
    def test_重ね描きの線は1本になる(self):
        """**太線は細い線の重ね描きで表される。**まとめないと同じ辺が何本にも数えられる。"""
        lines = [Segment(28.3, 34.0, 1125.0), Segment(28.7, 34.0, 1125.0), Segment(28.9, 34.0, 1125.0)]
        assert len(merge(lines)) == 1

    def test_繋がっている区間は繋ぐ(self):
        merged = merge([Segment(10.0, 0.0, 50.0), Segment(10.0, 50.5, 100.0)])
        assert len(merged) == 1
        assert merged[0].end == pytest.approx(100.0)

    def test_離れている区間は繋がない(self):
        merged = merge([Segment(10.0, 0.0, 50.0), Segment(10.0, 90.0, 100.0)])
        assert len(merged) == 2

    def test_座標がわずかに違う線でも区間の順に繋ぐ(self):
        """**ここを間違えると枠が見つからなくなる。**座標の順に並べただけでは
        区間の順にならないので、同じ辺の断片が繋がらない。"""
        lines = [
            Segment(10.0, 60.0, 100.0),
            Segment(10.4, 0.0, 60.2),
        ]
        merged = merge(lines)
        assert len(merged) == 1
        assert merged[0].start == pytest.approx(0.0)
        assert merged[0].end == pytest.approx(100.0)


class Test通しで覆う線があるか:
    def test_通しで覆っていれば真(self):
        assert spans([Segment(10.0, 0.0, 100.0)], 10.0, 20.0, 80.0) is True

    def test_足りなければ偽(self):
        assert spans([Segment(10.0, 0.0, 50.0)], 10.0, 20.0, 80.0) is False

    def test_別の座標の線は数えない(self):
        assert spans([Segment(40.0, 0.0, 100.0)], 10.0, 20.0, 80.0) is False


class Test輪郭線を見つける:
    def _frame(self, doc, rect, **kwargs):
        page = _page(doc, **kwargs)
        _draw_box(page, rect)
        horizontal, vertical = segments(page)
        return largest_closed_rect(merge(horizontal), merge(vertical), page.rect)

    def test_紙の縁から離れた矩形を枠として返す(self):
        with pymupdf.open() as doc:
            found = self._frame(doc, pymupdf.Rect(34, 28, 1125, 755))
        assert found is not None
        x0, y0, x1, y1 = found
        assert (x0, y0) == pytest.approx((34.0, 28.0))
        assert (x1, y1) == pytest.approx((1125.0, 755.0))

    def test_いちばん大きい矩形を選ぶ(self):
        with pymupdf.open() as doc:
            page = _page(doc)
            _draw_box(page, pymupdf.Rect(34, 28, 1125, 755))
            _draw_box(page, pymupdf.Rect(800, 600, 1100, 740))
            horizontal, vertical = segments(page)
            found = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
        assert found[2] == pytest.approx(1125.0)

    def test_紙の縁に接する矩形は枠にしない(self):
        """**JIS Z 8311 の輪郭線は用紙の縁の内側に引く。**紙いっぱいの矩形は
        紙そのものか背景の画像であって、輪郭線ではない。"""
        with pymupdf.open() as doc:
            page = _page(doc)
            _draw_box(page, pymupdf.Rect(0, 0, PAPER.width, PAPER.height))
            horizontal, vertical = segments(page)
            found = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
        assert found is None

    def test_縦の辺が無ければ枠にしない(self):
        """P011 匿名化v2 の 27〜30 ページがこの形(上下の横線だけ)。"""
        with pymupdf.open() as doc:
            page = _page(doc)
            page.draw_line(pymupdf.Point(34, 28), pymupdf.Point(1125, 28))
            page.draw_line(pymupdf.Point(34, 755), pymupdf.Point(1125, 755))
            horizontal, vertical = segments(page)
            found = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
        assert found is None

    def test_辺が途中で切れていれば枠にしない(self):
        with pymupdf.open() as doc:
            page = _page(doc)
            page.draw_line(pymupdf.Point(34, 28), pymupdf.Point(1125, 28))
            page.draw_line(pymupdf.Point(34, 755), pymupdf.Point(1125, 755))
            page.draw_line(pymupdf.Point(34, 28), pymupdf.Point(34, 755))
            page.draw_line(pymupdf.Point(1125, 28), pymupdf.Point(1125, 400))
            page.draw_line(pymupdf.Point(1125, 420), pymupdf.Point(1125, 755))
            horizontal, vertical = segments(page)
            found = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
        assert found is None

    def test_図形が無ければ枠は無い(self):
        with pymupdf.open() as doc:
            page = _page(doc)
            horizontal, vertical = segments(page)
            assert largest_closed_rect(merge(horizontal), merge(vertical), page.rect) is None

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_紙を回しても同じ大きさの枠が取れる(self, rotation: int):
        """**表示の向きに直してから数える。**K-26 で踏んだ轍を同じ形で踏まない。"""
        with pymupdf.open() as doc:
            page = _page(doc, rotation=rotation)
            _draw_box(page, pymupdf.Rect(34, 28, 1125, 755))
            horizontal, vertical = segments(page)
            found = largest_closed_rect(merge(horizontal), merge(vertical), page.rect)
        assert found is not None
        x0, y0, x1, y1 = found
        assert sorted((x1 - x0, y1 - y0)) == pytest.approx([727.0, 1091.0])


class Test表題欄:
    def test_枠の右下に接する矩形を返す(self):
        with pymupdf.open() as doc:
            page = _page(doc)
            frame = pymupdf.Rect(34, 28, 1125, 755)
            _draw_box(page, frame)
            _draw_box(page, pymupdf.Rect(800, 640, 1125, 755))
            horizontal, vertical = merge(segments(page)[0]), merge(segments(page)[1])
            found = title_block_rect(horizontal, vertical, (34.0, 28.0, 1125.0, 755.0))
        assert found is not None
        assert found[0] == pytest.approx(800.0)
        assert found[1] == pytest.approx(640.0)

    def test_右下に何も無ければ返さない(self):
        """P011 匿名化v2 の平面図のページがこの形(表題欄が枠の外にある)。"""
        with pymupdf.open() as doc:
            page = _page(doc)
            _draw_box(page, pymupdf.Rect(34, 28, 1125, 755))
            horizontal, vertical = merge(segments(page)[0]), merge(segments(page)[1])
            found = title_block_rect(horizontal, vertical, (34.0, 28.0, 1125.0, 755.0))
        assert found is None

    def test_左下にある区画は表題欄にしない(self):
        with pymupdf.open() as doc:
            page = _page(doc)
            _draw_box(page, pymupdf.Rect(34, 28, 1125, 755))
            _draw_box(page, pymupdf.Rect(34, 640, 400, 755))
            horizontal, vertical = merge(segments(page)[0]), merge(segments(page)[1])
            found = title_block_rect(horizontal, vertical, (34.0, 28.0, 1125.0, 755.0))
        assert found is None


class Test図面の種類:
    def test_名前から種類に振り分ける(self):
        assert classify("1階平面図") == "平面図"
        assert classify("A-A断面図") == "断面図"
        assert classify("内装仕上表") == "仕上表"

    def test_当たらない名前はその他にする(self):
        """**隠さずに数える。**種類の一覧のほうが図面に合っていないかもしれない。"""
        assert classify("特記事項") == "その他"

    def test_読めていなければNone(self):
        assert classify(None) is None
        assert classify("") is None

    def test_種類の一覧に重複した呼び方を入れない(self):
        names = [kind for kind, _ in KINDS]
        assert len(names) == len(set(names))
