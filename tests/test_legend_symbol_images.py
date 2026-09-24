"""K-21 の切り出しの試験。**合成の表だけを使う。**実図面は読まない。"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.build_legend_symbol_images import (
    blank_cells,
    build_sheets,
    crop,
    crop_rect,
    drawn_decoys,
    name_rows,
    page_drawings,
)
from tests.test_pdf_tables import draw_table


@pytest.fixture()
def legend_pdf(tmp_path):
    """「名称 / 記号」の表を 1 つ持つ合成の凡例。

    記号の升目が空の行(= 図形だけの行)と、群の見出しを入れてある。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    draw_table(
        page,
        origin=(40.0, 40.0),
        col_widths=(120.0, 80.0, 60.0),
        row_height=20.0,
        rows=(
            ("名称", "記号", ""),
            ("〈合成の群〉", "", ""),
            ("あいう", "ア", ""),
            ("かきく", "", ""),
            ("さしす", "イウ", ""),
        ),
    )
    path = tmp_path / "legend.pdf"
    doc.save(path)
    doc.close()
    return path


class TestNameRows:
    def test_名前の行だけを返し群の見出しは数えない(self, legend_pdf):
        rows = name_rows(legend_pdf, 1)
        assert [row["name"] for row in rows] == ["あいう", "かきく", "さしす"]

    def test_記号の升目に文字が無い行は群Bになる(self, legend_pdf):
        kinds = {row["name"]: row["kind"] for row in name_rows(legend_pdf, 1)}
        assert kinds["かきく"] == "群B 図形だけ"
        assert kinds["あいう"] == "群A 文字あり"

    def test_群の名前が付く(self, legend_pdf):
        rows = name_rows(legend_pdf, 1)
        assert {row["group"] for row in rows} == {"合成の群"}

    def test_切り出す幅は名前の列に掛からない(self, legend_pdf):
        """**答えが画像に写り込まないこと。**この試験がこの道具の要である。"""
        rows = name_rows(legend_pdf, 1)
        # 名前の列は x 40〜160、記号の列は x 160〜240。
        for row in rows:
            assert row["rect"][0] >= 160.0 - 0.5, row

    def test_切り出す高さはその行の高さ(self, legend_pdf):
        for row in name_rows(legend_pdf, 1):
            height = row["rect"][3] - row["rect"][1]
            assert 15.0 <= height <= 25.0


class TestDecoys:
    def test_罫線だけの空の升目を囮として拾う(self, legend_pdf):
        found = blank_cells(legend_pdf, 1, wanted=2)
        assert found
        assert all(entry["name"] is None for entry in found)
        assert all(entry["kind"] == "囮 空の升目" for entry in found)

    def test_欲しい数より多く返さない(self, legend_pdf):
        assert len(blank_cells(legend_pdf, 1, wanted=1)) == 1

    def test_合成の図形の囮は正解を持たない(self):
        shapes = drawn_decoys(5)
        assert len(shapes) == 5
        assert all(entry["name"] is None for entry, _ in shapes)
        assert all(pixmap.width > 0 for _, pixmap in shapes)


class TestSheets:
    def test_12件ずつの紙になる(self, tmp_path):
        _, pixmap = drawn_decoys(1)[0]
        tiles = [(f"S-{index + 1:03d}", pixmap) for index in range(25)]
        written = build_sheets(tiles, tmp_path / "sheets", sheet_zoom=1.0)
        assert [path.name for path in written] == [
            "sheet_01.png",
            "sheet_02.png",
            "sheet_03.png",
        ]
        assert all(path.exists() for path in written)


@pytest.fixture()
def overflow_pdf(tmp_path):
    """**切り出しの不良 2 件を合成で再現する。**実図面は読まない。

    - ``はみだし``: 記号がその行の升目より**上に飛び出している**(切ると絵が欠ける)。
    - ``もらい``: **次の行の記号**がこの行の升目の中まで入り込んでいる(切ると混ざる)。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    draw_table(
        page,
        origin=(40.0, 40.0),
        col_widths=(120.0, 80.0, 60.0),
        row_height=20.0,
        rows=(
            ("名称", "記号", ""),
            ("はみだし", "", ""),
            ("もらい", "", ""),
            ("となり", "", ""),
        ),
    )
    # 「はみだし」の行(y 60〜80)。升目の上辺を 3pt またぐ四角。
    page.draw_rect(pymupdf.Rect(170.0, 57.0, 200.0, 78.0), width=0.8)
    # 「もらい」の行(y 80〜100)と「となり」の行(y 100〜120)。
    # となりの記号(中心 y=102)が上に 2pt はみ出して「もらい」の升目に入る。
    page.draw_circle(pymupdf.Point(185.0, 102.0), 4.0, width=0.8)
    # となりの行の**文字**。凡例の記号には線でなく文字で描かれたものがある。
    page.insert_text(pymupdf.Point(210.0, 108.0), "E", fontsize=14)
    path = tmp_path / "overflow.pdf"
    doc.save(path)
    doc.close()
    return path


class TestCropDefects:
    """K-22 (a)。**切り出しの不良 2 件**を直したことを固定する。"""

    def _row(self, pdf, name):
        return next(row for row in name_rows(pdf, 1) if row["name"] == name)

    def test_升目からはみ出した記号を切り落とさない(self, overflow_pdf):
        row = self._row(overflow_pdf, "はみだし")
        with pymupdf.open(overflow_pdf) as doc:
            page = doc[0]
            rect = crop_rect(page, row["rect"], page_drawings(page))
        assert rect.y0 <= 57.0, rect
        assert rect.x1 >= 200.0, rect

    def test_となりの記号は白で消す(self, overflow_pdf):
        row = self._row(overflow_pdf, "もらい")
        with pymupdf.open(overflow_pdf) as doc:
            page = doc[0]
            drawings = page_drawings(page)
            rect = crop_rect(page, row["rect"], drawings)
            pixmap = crop(page, row["rect"], drawings, zoom=4.0)
        # となりの円は y 98〜106。升目の下辺(y=100)より上の分が入り込んでいる。
        top = int((98.0 - rect.y0) * 4.0)
        bottom = min(pixmap.height, int((100.0 - rect.y0) * 4.0))
        left = int((181.0 - rect.x0) * 4.0)
        right = min(pixmap.width, int((189.0 - rect.x0) * 4.0))
        dark = [
            (x, y)
            for y in range(max(top, 0), max(bottom, 0))
            for x in range(max(left, 0), max(right, 0))
            if pixmap.pixel(x, y)[0] < 128
        ]
        assert not dark, f"となりの記号が残っている: {len(dark)} 画素"

    def test_となりの行の文字も白で消す(self, overflow_pdf):
        """**文字で描かれた記号**は、描画命令を見ているだけでは消えない。"""
        row = self._row(overflow_pdf, "もらい")
        with pymupdf.open(overflow_pdf) as doc:
            page = doc[0]
            drawings = page_drawings(page)
            rect = crop_rect(page, row["rect"], drawings)
            pixmap = crop(page, row["rect"], drawings, zoom=4.0)
        # 升目の下辺(y=100)の罫線は残す決まりなので、そこに掛からない帯で見る。
        top = max(int((94.0 - rect.y0) * 4.0), 0)
        bottom = min(pixmap.height, int((99.0 - rect.y0) * 4.0))
        left = max(int((205.0 - rect.x0) * 4.0), 0)
        right = min(pixmap.width, int((220.0 - rect.x0) * 4.0))
        dark = [
            (x, y)
            for y in range(top, max(bottom, 0))
            for x in range(left, max(right, 0))
            if pixmap.pixel(x, y)[0] < 128
        ]
        assert not dark, f"となりの行の文字が残っている: {len(dark)} 画素"

    def test_罫線は消さない(self, overflow_pdf):
        """**表の枠線まで白くしない。**枠は読む側の手がかりである。"""
        row = self._row(overflow_pdf, "もらい")
        with pymupdf.open(overflow_pdf) as doc:
            page = doc[0]
            drawings = page_drawings(page)
            rect = crop_rect(page, row["rect"], drawings)
            pixmap = crop(page, row["rect"], drawings, zoom=4.0)
        band = int((100.0 - rect.y0) * 4.0)
        dark = [
            x
            for x in range(pixmap.width)
            for y in range(max(band - 3, 0), min(band + 3, pixmap.height))
            if pixmap.pixel(x, y)[0] < 128
        ]
        assert dark, "升目の下辺が消えている"
