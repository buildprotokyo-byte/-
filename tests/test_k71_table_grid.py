"""K-71 作業 3 周 1: 罫線の表の罫線を機械が読んで台帳に足す(`draft/table_grid.py`)。

固定すること: **AI が表の中身を読んだときだけ足す。**大きい箱・表の四角を囲んだだけの箱(中身を読まない囮)では 1 本も足さず、
表の読了率も上がらない。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks import erase_check as ec
from draft import readthrough as rt
from draft import table_grid

W, H = 600, 400
X0, Y0, CW, RH, COLS, ROWS = 100, 100, 80, 30, 4, 5


@pytest.fixture
def table_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    for r in range(ROWS + 1):
        page.draw_line((X0, Y0 + r * RH), (X0 + COLS * CW, Y0 + r * RH))
    for c in range(COLS + 1):
        page.draw_line((X0 + c * CW, Y0), (X0 + c * CW, Y0 + ROWS * RH))
    for r in range(ROWS):
        for c in range(COLS):
            page.insert_text((X0 + c * CW + 8, Y0 + r * RH + 20), f"A{r}{c}", fontsize=10)
    target = tmp_path / "表.pdf"
    doc.save(target)
    doc.close()
    return target


def _scale() -> float:
    return ec.WIDTH_PX / W


def _word_elements(page: pymupdf.Page, share: float) -> list[dict]:
    """文字の層の語の位置そのままの AI の文字の要素(先頭から ``share`` の割合だけ)。"""
    s = _scale()
    words = page.get_text("words")
    take = words[: int(round(len(words) * share))]
    return [{"種類": "文字", "内容": w[4], "位置": [w[0] * s, w[1] * s, w[2] * s, w[3] * s]} for w in take]


def _table_rate(page: pymupdf.Page, elements: list[dict], grid: bool) -> float | None:
    out = rt.page_readthrough(page, 1, elements, with_unread=False, machine_grid=grid)
    return out["別の切り口"]["表"]["読了率"]


def test_中身を読んだ表は罫線を足して表の読了率が上がる(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        assert rt._table_rects(page)[0], "合成の表が守りを通ること"
        elements = _word_elements(page, 1.0)
        added, notes = table_grid.grid_elements(page, 1, elements)
        # 表の四角の縁に乗る外枠の線は、中心が四角の外に出ることがある(表の切り口も同じ判定で数えない)
        assert ROWS + COLS - 1 <= len(added) <= (ROWS + 1) + (COLS + 1)
        assert all(e["出どころ"] == table_grid.SOURCE and e["種類"] == "線" for e in added)
        before = _table_rate(page, elements, grid=False)
        after = _table_rate(page, elements, grid=True)
        assert before is not None and after is not None
        assert after > before
        assert after == 1.0


def test_中身を読んでいない表には足さない(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        few = _word_elements(page, 0.4)  # 証拠 0.5 に届かない
        added, notes = table_grid.grid_elements(page, 1, few)
        assert added == []
        assert notes[0]["足さなかった理由"]
        assert _table_rate(page, few, grid=True) == _table_rate(page, few, grid=False)


def test_表の四角を囲んだだけの囮では足さない(table_pdf: Path) -> None:
    s = _scale()
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        rect = rt._table_rects(page)[0][0]
        decoy = [{"種類": "表", "位置": [v * s for v in rect]}]
        big = [{"種類": "大きい箱", "位置": [0, 0, W * s, H * s]}]
        for fake in (decoy, big):
            added, _ = table_grid.grid_elements(page, 1, fake)
            assert added == []
            assert (_table_rate(page, fake, grid=True) or 0.0) < 0.10


def test_大きい文字の箱は証拠にならない(table_pdf: Path) -> None:
    """面積の上限 1% を超える「文字」の箱で表を囲んでも、語に印は付かず罫線は足さない。"""
    s = _scale()
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        rect = rt._table_rects(page)[0][0]
        fake = [{"種類": "文字", "位置": [v * s for v in rect]}]
        added, _ = table_grid.grid_elements(page, 1, fake)
        assert added == []


def test_升目の縁に乗らない線は足さない(tmp_path: Path) -> None:
    """表と誤認された図の中の線のように、升目の縁に乗らない線は罫線として足さない。"""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    for r in range(ROWS + 1):
        page.draw_line((X0, Y0 + r * RH), (X0 + COLS * CW, Y0 + r * RH))
    for c in range(COLS + 1):
        page.draw_line((X0 + c * CW, Y0), (X0 + c * CW, Y0 + ROWS * RH))
    for r in range(ROWS):
        for c in range(COLS):
            page.insert_text((X0 + c * CW + 8, Y0 + r * RH + 20), f"A{r}{c}", fontsize=10)
    # 升目の中の短い横線(縁に乗らない)
    page.draw_line((X0 + 10, Y0 + 8), (X0 + 60, Y0 + 8))
    target = tmp_path / "中の線.pdf"
    doc.save(target)
    doc.close()
    with pymupdf.open(target) as d:
        p = d.load_page(0)
        added, _ = table_grid.grid_elements(p, 1, _word_elements(p, 1.0))
        s = _scale()
        inner_y = (Y0 + 8) * s
        assert added, "縁の罫線は足す"
        assert not any(abs(e["位置"][1] - inner_y) < 1 and abs(e["位置"][3] - inner_y) < 1 for e in added)
