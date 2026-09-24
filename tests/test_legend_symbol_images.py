"""K-21 の切り出しの試験。**合成の表だけを使う。**実図面は読まない。"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.build_legend_symbol_images import (
    blank_cells,
    build_sheets,
    drawn_decoys,
    name_rows,
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
