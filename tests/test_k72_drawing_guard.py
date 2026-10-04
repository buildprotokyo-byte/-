"""K-72 作業 B: 図を表と誤認する守り(`docs/k72_readrate_guard_criteria.md`)。

固定すること:
- 升目 1 つあたり、升目の縁に乗らない線が 100 本以上の「表」は、図を表と誤認したものとして表から外す。
- 升目に小さい見本の図が入った本物の表(凡例のような表)は外さない。
- 守りは図形の層だけで決まる(AI の読みに依らない)。外れた表には機械の罫線も足さない。前の守り(``drawing_guard=False``)に戻せる。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from draft import readthrough as rt
from draft import table_grid

W, H = 600, 400
X0, Y0, CW, RH, COLS, ROWS = 100, 100, 80, 30, 2, 2  # 升目 4 つ


def _grid(page: pymupdf.Page) -> None:
    for r in range(ROWS + 1):
        page.draw_line((X0, Y0 + r * RH), (X0 + COLS * CW, Y0 + r * RH))
    for c in range(COLS + 1):
        page.draw_line((X0 + c * CW, Y0), (X0 + c * CW, Y0 + ROWS * RH))
    for r in range(ROWS):
        for c in range(COLS):
            page.insert_text((X0 + c * CW + 4, Y0 + r * RH + 10), f"A{r}{c}", fontsize=7)


def _strokes(page: pymupdf.Page, per_cell: int) -> None:
    """各升目の中に、縁に乗らない短い斜めの線を ``per_cell`` 本ずつ描く(図の線。1 本ずつ別の描画にして、ハッチングにまとめさせない)。"""
    for r in range(ROWS):
        for c in range(COLS):
            x, y = X0 + c * CW + 40, Y0 + r * RH + 14
            for k in range(per_cell):
                dx = (k % 30) * 1.2
                dy = (k // 30) * 3.0
                page.draw_line((x + dx, y + dy), (x + dx + 1.0, y + dy + 1.5), width=0.2)


def _pdf(tmp_path: Path, per_cell: int, name: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _grid(page)
    if per_cell:
        _strokes(page, per_cell)
    target = tmp_path / name
    doc.save(target)
    doc.close()
    return target


def _words(page: pymupdf.Page) -> list[dict]:
    s = 2000 / W
    return [{"種類": "文字", "内容": w[4], "位置": [w[0] * s, w[1] * s, w[2] * s, w[3] * s]}
            for w in page.get_text("words")]


def test_升目あたり図の線が多い表は表から外す(tmp_path: Path) -> None:
    target = _pdf(tmp_path, 110, "図.pdf")
    with pymupdf.open(target) as doc:
        page = doc.load_page(0)
        before, _ = rt._ruled_tables(page, drawing_guard=False)
        assert len(before) == 1, "前の守り(升目の 6 割以上に文字)は通ってしまう"
        after, note = rt._ruled_tables(page)
        assert after == []
        assert "図と見た表 1 個" in note
        assert note.startswith(rt.CANNOT_MEASURE), "表が残らないページは 0 でなく測れない"
        out = rt.page_readthrough(page, 1, _words(page), with_unread=False)
        assert out["別の切り口"]["表"]["読了率"] is None
        # 外れた表には機械の罫線も足さない
        added, _ = table_grid.grid_elements(page, 1, _words(page))
        assert added == []
        added_old, _ = table_grid.grid_elements(page, 1, _words(page), drawing_guard=False)
        assert added_old


def test_小さい見本の図が入った本物の表は外さない(tmp_path: Path) -> None:
    target = _pdf(tmp_path, 40, "凡例.pdf")
    with pymupdf.open(target) as doc:
        page = doc.load_page(0)
        after, _ = rt._ruled_tables(page)
        assert len(after) == 1


def test_罫線と文字だけの表は外さない(tmp_path: Path) -> None:
    target = _pdf(tmp_path, 0, "表.pdf")
    with pymupdf.open(target) as doc:
        page = doc.load_page(0)
        assert len(rt._ruled_tables(page)[0]) == 1
        assert rt._ruled_tables(page)[0] == rt._ruled_tables(page, drawing_guard=False)[0]


def test_守りは読みに依らない(tmp_path: Path) -> None:
    """守りは図形の層だけで決める。readthrough の前後の切り替えで、外れた表の数だけが変わる。"""
    target = _pdf(tmp_path, 110, "図2.pdf")
    with pymupdf.open(target) as doc:
        page = doc.load_page(0)
        s = 2000 / W
        fakes = ([], _words(page), [{"種類": "表", "位置": [X0 * s, Y0 * s, (X0 + COLS * CW) * s, (Y0 + ROWS * RH) * s]}])
        for fake in fakes:
            new = rt.page_readthrough(page, 1, fake, with_unread=False)
            old = rt.page_readthrough(page, 1, fake, with_unread=False, drawing_guard=False)
            assert new["別の切り口"]["表"]["読了率"] is None
            assert old["別の切り口"]["表"].get("数える", 0) > 0
