"""K-73 作業 3(b): 機械の罫線を足す条件を「AI の文字の中身が一致する」に厳しくする(`docs/k73_grid_text_criteria.md`)。

固定すること:
- 位置が合っていても、AI の内容が文字の層の語と一致しなければ証拠にならない(罫線を足さない)。
- 内容を持たない箱(でたらめに置いた箱の囮)では足さない。
- 内容が語と一致すれば足す(全角・半角・空白の違いは正規化でそろえる。1 つの要素に複数の語をまとめて読んでもよい)。
- ``text_match=False`` で前(位置だけ)に戻せる。分母(数える図形)は前後で同じ。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from draft import readthrough as rt
from draft import table_grid

W, H = 600, 400
X0, Y0, CW, RH, COLS, ROWS = 100, 100, 80, 30, 4, 5
S = 2000 / W


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


def _boxes(page: pymupdf.Page, content) -> list[dict]:
    out = []
    for i, w in enumerate(page.get_text("words")):
        e = {"種類": "文字", "位置": [w[0] * S, w[1] * S, w[2] * S, w[3] * S]}
        text = content(i, w[4])
        if text is not None:
            e["内容"] = text
        out.append(e)
    return out


def test_位置が合っていても中身が違えば足さない(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        wrong = _boxes(page, lambda i, t: "Z" + str(i % 3))
        added, notes = table_grid.grid_elements(page, 1, wrong)
        assert added == []
        assert notes[0]["足さなかった理由"]
        old, _ = table_grid.grid_elements(page, 1, wrong, text_match=False)
        assert old, "前(位置だけ)は足していた"


def test_内容を持たない箱では足さない(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        empty = _boxes(page, lambda i, t: None)
        added, _ = table_grid.grid_elements(page, 1, empty)
        assert added == []


def test_中身が一致すれば足す_正規化と行まとめ(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        # 全角にして空白を挟む(NFKC と空白の除去でそろう)
        wide = _boxes(page, lambda i, t: " ".join(t.translate(str.maketrans("A0123456789", "Ａ０１２３４５６７８９"))))
        added, notes = table_grid.grid_elements(page, 1, wide)
        assert added
        assert notes[0]["AI の文字の中身が一致した語の割合"] == 1.0
        # 隣り合う 2 語を 1 つの要素で読む(内容に 2 語とも入る。面積の上限 1% に収まる大きさ)
        words = sorted(page.get_text("words"), key=lambda w: (round(w[3]), w[0]))
        pairs = [words[i:i + 2] for i in range(0, len(words), 2)]
        line = [{"種類": "文字", "内容": " ".join(x[4] for x in ws),
                 "位置": [min(x[0] for x in ws) * S, min(x[1] for x in ws) * S,
                          max(x[2] for x in ws) * S, max(x[3] for x in ws) * S]} for ws in pairs]
        added_line, _ = table_grid.grid_elements(page, 1, line)
        assert added_line


def test_分母は前後で同じ(table_pdf: Path) -> None:
    with pymupdf.open(table_pdf) as doc:
        page = doc.load_page(0)
        wrong = _boxes(page, lambda i, t: "Z")
        before = rt.page_readthrough(page, 1, wrong, with_unread=False, text_match=False)
        after = rt.page_readthrough(page, 1, wrong, with_unread=False)
        assert before["数える図形"] == after["数える図形"]
        assert before["別の切り口"]["表"]["数える"] == after["別の切り口"]["表"]["数える"]
        assert after["別の切り口"]["表"]["拾えた"] < before["別の切り口"]["表"]["拾えた"]
        assert after["墨の量で見た読了率"]["線(長さ)"]["全部"] == before["墨の量で見た読了率"]["線(長さ)"]["全部"]
