"""凡例の「記号」の升目を **1 件ずつ画像に切り出す**(K-21)。

なぜ画像か
----------
K-20 で分かったのは、**凡例の名前の行 86 行のうち 19 行は記号の升目に文字が無い**
ということである。丸や四角の絵だけで描かれている。**文字で引き当てる道では
原理的に取れない。**その 19 件に、建築の知識で名前が付くかどうかを測りたい。
測るには、読む側に**絵を見せる**しかない。

この道具が守ること
------------------
1. **答えが画像に写り込まないこと。**切り出す幅は「記号」の列の x の幅ぴったりで、
   **名前の列には決して掛からない。**(升目が横に潰れている行でも、列の見出しの
   矩形から x を取るので幅は変わらない。)
2. **並びを混ぜること。**群の並びや行の順から答えを当てられないようにする。
   種は基準に書いた ``20260924`` で固定。
3. **囮を混ぜること。**凡例に無いものを 10 件混ぜる(罫線だけの空の升目 5・
   凡例に無い合成の図形 5)。**囮に名前が付いたら、その条件の名前は疑う。**

出すもの
--------
* ``--sheets`` … 12 件ずつ並べた紙(コンタクトシート)の PNG。**読む側に渡すのはこれだけ。**
* ``--answers`` … 通し番号と正解(凡例が刷っている名前)の対応。
  **図面から取った文字なので、リポジトリに置かない。**共有フォルダのパスを渡すこと。

実行::

    python -m benchmarks.build_legend_symbol_images --pdf <図面.pdf> --page 22 \\
        --sheets <作業用フォルダ> --answers <共有フォルダ>/answers.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path
from typing import Any

import pymupdf

from axes.image_axis.pdf_tables import find_tables
from benchmarks.build_legend_lookup import (
    _GROUP_HEADING,
    _name_without_note,
    _norm,
    _usable_code,
    _usable_name,
)

#: 並べ替えの種。**基準に書いた値。結果を見てから変えない。**
SHUFFLE_SEED = 20260924

#: 1 枚に並べる件数。
PER_SHEET = 12

#: 紙の升目 1 つの大きさ(pt)。
TILE_W, TILE_H = 300.0, 230.0

#: 絵を置く枠(pt)。下に番号を書く余白を残す。
BOX_W, BOX_H = 286.0, 176.0

#: 切り出しの拡大率。細い線が消えない程度に大きく取る。
CROP_ZOOM = 8.0

#: 紙を PNG にするときの拡大率。
SHEET_ZOOM = 2.0

#: 切り出しに足す余白(pt)。K-22 (a) で足した。線の太さの分だけ外を入れる。
CROP_MARGIN = 1.0

#: 囮の数(罫線だけの空の升目 / 凡例に無い合成の図形)。
BLANK_DECOYS, DRAWN_DECOYS = 5, 5


def symbol_column(region: Any, head: int, code_column: int) -> tuple[float, float, float, float]:
    """**記号の列の x の幅**を、見出しの升目ではなく**各行の升目の中央値**から決める。

    K-22 (b)。P011 の 22 ページの 3 つ目の表は、**見出しの「記号」の升目だけが
    となりの「例」の列とつながっている。**見出しから x を取ると、その表の行は
    全部**例の列まで一緒に切り出される**(読む側は例に描かれた別の器具を読んでしまう)。
    中央値なら、つながっている升目が 1 つあっても動かない。

    名前の列に掛からないことは、見出しの「記号」の升目の左端より左へ出さないことで守る。
    """
    spans = [
        region.rows[index][code_column].rect_pt
        for index in range(head + 1, region.row_count)
    ]
    header = region.rows[head][code_column].rect_pt
    if not spans:
        return header
    left = max(statistics.median(span[0] for span in spans), header[0])
    right = min(statistics.median(span[2] for span in spans), header[2])
    if right - left < 1.0:
        return header
    return (left, header[1], right, header[3])


def name_rows(pdf_path: Path | str, page_number: int) -> list[dict[str, Any]]:
    """「名称 / 記号」の表から、**名前の行を 1 行ずつ**返す。

    K-20 の :func:`benchmarks.build_legend_lookup.symbol_rows_from_tables` と
    **同じ数え方**で群を決める。数が食い違ったら、どちらかが壊れている。

    返す ``rect`` は「**記号の列の x の幅 × その行の y の幅**」である。
    行ごとの升目の矩形をそのまま使うと、升目が横に潰れている行(幅 6pt ほど)で
    絵が切れる。列の見出しの矩形から x を取ればその心配が無く、しかも
    **名前の列に掛からないことが列の定義から保証される。**
    """
    rows: list[dict[str, Any]] = []
    for table_index, region in enumerate(find_tables(pdf_path, page_number - 1)):
        texts = region.texts()
        head = next(
            (
                index
                for index, row in enumerate(texts[:2])
                if {"名称", "記号"} <= {_norm(cell) for cell in row}
            ),
            None,
        )
        if head is None:
            continue
        header = [_norm(cell) for cell in texts[head]]
        name_column = header.index("名称")
        code_column = header.index("記号")
        column = symbol_column(region, head, code_column)
        group: str | None = None
        for row_index in range(head + 1, region.row_count):
            row = region.rows[row_index]
            raw_name = row[name_column].text.strip()
            if not raw_name:
                continue
            heading = _GROUP_HEADING.match(raw_name)
            if heading:
                group = heading.group(1).strip()
                continue
            code = next((c.text for c in row[name_column + 1 :] if c.text.strip()), "")
            name = _name_without_note(raw_name)
            if not code:
                kind = "群B 図形だけ"
            elif not _usable_name(name):
                kind = "群C 決まらない"
            elif not _usable_code(code):
                kind = "群C 決まらない"
            else:
                kind = "群A 文字あり"
            span = row[name_column].rect_pt
            rows.append(
                {
                    "table_index": table_index,
                    "row_index": row_index,
                    "kind": kind,
                    "name": name,
                    "code": code.strip(),
                    "group": group,
                    "rect": (column[0], span[1], column[2], span[3]),
                }
            )
    return rows


def blank_cells(pdf_path: Path | str, page_number: int, wanted: int) -> list[dict[str, Any]]:
    """**罫線だけで中身が無い升目**を囮として集める。

    「名称 / 記号」の表の、見出しが空の列(記号の右にある余りの列)から取る。
    **正解は「凡例に無い」である。**ここに名前が付いたら、読む側は絵が無くても
    名前を作っている。
    """
    found: list[dict[str, Any]] = []
    for table_index, region in enumerate(find_tables(pdf_path, page_number - 1)):
        texts = region.texts()
        head = next(
            (
                index
                for index, row in enumerate(texts[:2])
                if {"名称", "記号"} <= {_norm(cell) for cell in row}
            ),
            None,
        )
        if head is None:
            continue
        header = [_norm(cell) for cell in texts[head]]
        spare = [i for i, cell in enumerate(header) if not cell and i > header.index("記号")]
        for row_index in range(head + 1, region.row_count):
            row = region.rows[row_index]
            for column_index in spare:
                cell = row[column_index]
                x0, y0, x1, y1 = cell.rect_pt
                if cell.text.strip() or cell.merged_with_above:
                    continue
                if x1 - x0 < 10.0 or y1 - y0 < 8.0:
                    continue
                found.append(
                    {
                        "kind": "囮 空の升目",
                        "name": None,
                        "code": "",
                        "group": None,
                        "table_index": table_index,
                        "row_index": row_index,
                        "rect": cell.rect_pt,
                    }
                )
                if len(found) >= wanted:
                    return found
    return found


def drawn_decoys(count: int) -> list[tuple[dict[str, Any], pymupdf.Pixmap]]:
    """**凡例に無い図形**を描いて囮にする。実図面から取らない。

    正解は「凡例に無い」。読む側がこれに設備の名前を付けたら、**絵から名前を
    作っている**ということになる。
    """
    shapes: list[tuple[dict[str, Any], pymupdf.Pixmap]] = []
    with pymupdf.open() as doc:
        for index in range(count):
            page = doc.new_page(width=50, height=30)
            middle = pymupdf.Point(25, 15)
            if index == 0:
                page.draw_polyline(
                    [
                        pymupdf.Point(25, 6),
                        pymupdf.Point(33, 22),
                        pymupdf.Point(17, 22),
                        pymupdf.Point(25, 6),
                    ],
                    width=0.8,
                )
                page.draw_line(pymupdf.Point(20, 10), pymupdf.Point(30, 20), width=0.8)
                page.draw_line(pymupdf.Point(30, 10), pymupdf.Point(20, 20), width=0.8)
            elif index == 1:
                page.draw_polyline(
                    [
                        pymupdf.Point(25, 5),
                        pymupdf.Point(34, 12),
                        pymupdf.Point(31, 23),
                        pymupdf.Point(19, 23),
                        pymupdf.Point(16, 12),
                        pymupdf.Point(25, 5),
                    ],
                    width=0.8,
                )
            elif index == 2:
                for size in (10, 5):
                    page.draw_polyline(
                        [
                            pymupdf.Point(25, 15 - size),
                            pymupdf.Point(25 + size, 15),
                            pymupdf.Point(25, 15 + size),
                            pymupdf.Point(25 - size, 15),
                            pymupdf.Point(25, 15 - size),
                        ],
                        width=0.8,
                    )
            elif index == 3:
                page.draw_circle(middle, 9, width=0.8)
                page.draw_line(pymupdf.Point(17, 11), pymupdf.Point(33, 11), width=0.8)
                page.draw_line(pymupdf.Point(17, 19), pymupdf.Point(33, 19), width=0.8)
            else:
                page.draw_rect(pymupdf.Rect(15, 7, 35, 23), width=0.8)
                page.draw_bezier(
                    pymupdf.Point(17, 15),
                    pymupdf.Point(22, 8),
                    pymupdf.Point(28, 22),
                    pymupdf.Point(33, 15),
                    width=0.8,
                )
            shapes.append(
                (
                    {
                        "kind": "囮 合成の図形",
                        "name": None,
                        "code": "",
                        "group": None,
                        "table_index": None,
                        "row_index": index,
                        "rect": None,
                    },
                    page.get_pixmap(matrix=pymupdf.Matrix(CROP_ZOOM, CROP_ZOOM)),
                )
            )
    return shapes


def page_drawings(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """そのページの描画命令の外接矩形を、**回転を掛けたあと**の座標で返す。

    ``page.get_drawings()`` は**回転を掛ける前**の座標を返す。``find_tables`` の
    升目は**掛けたあと**なので、そろえないと比べられない(K-20 で 2 度間違えた)。
    """
    matrix = page.rotation_matrix
    return [pymupdf.Rect(drawing["rect"]) * matrix for drawing in page.get_drawings()]


def page_words(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """そのページの**語**の外接矩形を、回転を掛けたあとの座標で返す。

    K-22 (a)。凡例の記号には**線ではなく文字で描かれているもの**がある
    (S-080 に入り込んでいたのは、となりの行の文字だった)。描画命令だけを見て
    消すと、文字でできた記号は残ってしまう。
    """
    matrix = page.rotation_matrix
    return [pymupdf.Rect(word[:4]) * matrix for word in page.get_text("words")]


def _centre(rect: pymupdf.Rect) -> pymupdf.Point:
    return pymupdf.Point((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)


def _fits(rect: pymupdf.Rect, cell: pymupdf.Rect, slack: float = 1.5) -> bool:
    """**その升目の記号と呼べる大きさか。**

    表全体を囲う枠のように、升目よりはるかに大きい命令の中心がたまたま升目に
    入ることがある。それを「その升目のもの」に入れると、切り出しが表ごと大きくなる。
    """
    return rect.width <= cell.width * slack and rect.height <= cell.height * slack


def _own_and_foreign(
    cell: pymupdf.Rect, drawings: Iterable[pymupdf.Rect]
) -> tuple[pymupdf.Rect, list[pymupdf.Rect]]:
    """**その升目のもの**と、**よその行のもの**に分ける。

    分ける基準は**外接矩形の中心がその升目の中にあるか**である。中心で分けるので、
    升目から少しはみ出して描かれた記号は「その升目のもの」に入り(切り落とさない)、
    となりの行の記号は、この升目に入り込んでいても「よそのもの」に入る(消せる)。
    """
    own = pymupdf.Rect()
    foreign: list[pymupdf.Rect] = []
    for rect in drawings:
        if cell.contains(_centre(rect)) and _fits(rect, cell):
            own |= rect
        elif rect.intersects(cell):
            foreign.append(rect)
    return own, foreign


def crop_rect(
    page: pymupdf.Page,
    rect: tuple[float, float, float, float],
    drawings: Iterable[pymupdf.Rect],
    margin: float = CROP_MARGIN,
) -> pymupdf.Rect:
    """切り出す矩形。**升目と、その升目の記号の両方が入る大きさ**にする。

    K-22 (a)。升目の矩形だけで切ると、升目より大きく描かれた記号が欠ける
    (S-087 は上辺が落ちて、四角ではなく塗りつぶしのくさびに見えていた)。
    """
    cell = pymupdf.Rect(*rect)
    own, _ = _own_and_foreign(cell, drawings)
    box = cell if own.is_empty else (cell | own)
    return pymupdf.Rect(box.x0 - margin, box.y0 - margin, box.x1 + margin, box.y1 + margin)


def _erasable(rect: pymupdf.Rect, cell: pymupdf.Rect) -> bool:
    """**消してよい「よそのもの」か。**

    罫線は消さない。枠は読む側の手がかりであり、消すと升目の大きさが分からなくなる。
    見分け方は**升目より小さいこと**で、表を横断する罫線は升目より長いので残る。
    """
    return (
        0 < rect.width < cell.width
        and 0 < rect.height < cell.height
        and rect.get_area() < cell.get_area() * 0.6
    )


def crop(
    page: pymupdf.Page,
    rect: tuple[float, float, float, float],
    drawings: Iterable[pymupdf.Rect],
    margin: float = CROP_MARGIN,
    zoom: float = CROP_ZOOM,
    words: Iterable[pymupdf.Rect] | None = None,
) -> pymupdf.Pixmap:
    """升目を切り出す。**よその行の記号は白で消す。**

    K-22 (a)。升目の高さが中央値より高い行では、となりの行の記号が入り込む
    (S-080 で実際に混ざった)。読む側はそれを「1 つの絵」として読んでしまう。
    """
    cell = pymupdf.Rect(*rect)
    drawings = list(drawings)
    if words is None:
        words = page_words(page)
    box = crop_rect(page, rect, drawings, margin)
    # **よそのものは「升目に掛かるもの」ではなく「切り出しに掛かるもの」で拾う。**
    # 余白を足したぶん、升目には掛からないがこの絵には写るものが出る。
    # 丸は何本もの曲線に分かれているので、升目で拾うと一部しか消えない(実際そうなった)。
    foreign = [
        other
        for other in list(drawings) + list(words)
        if other.intersects(box) and not (cell.contains(_centre(other)) and _fits(other, cell))
    ]
    pixmap = page.get_pixmap(clip=box, matrix=pymupdf.Matrix(zoom, zoom))
    for other in foreign:
        if not _erasable(other, cell):
            continue
        hit = pymupdf.Rect(other) & box
        if hit.is_empty:
            continue
        # ``set_rect`` は**絵の中の座標ではなく紙の上の座標**で受け取る
        # (``pixmap.x`` / ``pixmap.y`` が原点)。ここを忘れると 1 画素も消えない。
        pixmap.set_rect(
            pymupdf.IRect(
                pixmap.x + max(int((hit.x0 - box.x0) * zoom) - 1, 0),
                pixmap.y + max(int((hit.y0 - box.y0) * zoom) - 1, 0),
                pixmap.x + min(int((hit.x1 - box.x0) * zoom) + 2, pixmap.width),
                pixmap.y + min(int((hit.y1 - box.y0) * zoom) + 2, pixmap.height),
            ),
            (255, 255, 255),
        )
    return pixmap


def build_sheets(
    tiles: list[tuple[str, pymupdf.Pixmap]], out_dir: Path, sheet_zoom: float = SHEET_ZOOM
) -> list[Path]:
    """12 件ずつ並べた紙にする。**升目ごとに枠と通し番号**を入れる。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sheet_index in range(0, len(tiles), PER_SHEET):
        batch = tiles[sheet_index : sheet_index + PER_SHEET]
        with pymupdf.open() as doc:
            page = doc.new_page(width=TILE_W * 4, height=TILE_H * 3)
            for position, (label, pixmap) in enumerate(batch):
                column, row = position % 4, position // 4
                left, top = column * TILE_W, row * TILE_H
                box = pymupdf.Rect(
                    left + (TILE_W - BOX_W) / 2,
                    top + 6,
                    left + (TILE_W + BOX_W) / 2,
                    top + 6 + BOX_H,
                )
                page.draw_rect(box, width=0.6, color=(0.6, 0.6, 0.6))
                scale = min(BOX_W / pixmap.width, BOX_H / pixmap.height, 1.0)
                width, height = pixmap.width * scale, pixmap.height * scale
                middle = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
                page.insert_image(
                    pymupdf.Rect(
                        middle[0] - width / 2,
                        middle[1] - height / 2,
                        middle[0] + width / 2,
                        middle[1] + height / 2,
                    ),
                    pixmap=pixmap,
                )
                page.insert_text(
                    pymupdf.Point(box.x0 + 4, box.y1 + 22),
                    label,
                    fontsize=18,
                    fontname="helv",
                )
            path = out_dir / f"sheet_{sheet_index // PER_SHEET + 1:02d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(sheet_zoom, sheet_zoom)).save(path)
            written.append(path)
    return written


#: 囮の線の太さ(pt)。**22 ページの記号の線の太さの中央値**に合わせてある。
#: K-21 では囮の線が太く、それだけで 3 条件に見破られた。
DECOY_WIDTH = 0.48


class _Pen:
    """**回転を掛けたあとの座標**で描くための筆。

    ``page.new_shape()`` は**回転を掛ける前**の座標で描く。凡例のページは 270 度
    回っているので、そのまま描くと紙の外へ出る(実際に出た)。点を 1 つずつ
    戻してから渡す。
    """

    def __init__(self, page: pymupdf.Page) -> None:
        self._shape = page.new_shape()
        self._back = ~page.rotation_matrix

    def _point(self, x: float, y: float) -> pymupdf.Point:
        return pymupdf.Point(x, y) * self._back

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self._shape.draw_line(self._point(x0, y0), self._point(x1, y1))

    def polyline(self, points: Iterable[tuple[float, float]]) -> None:
        self._shape.draw_polyline([self._point(x, y) for x, y in points])

    def circle(self, x: float, y: float, radius: float) -> None:
        self._shape.draw_circle(self._point(x, y), radius)

    def rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])

    def commit(self, width: float = DECOY_WIDTH) -> None:
        self._shape.finish(width=width)
        self._shape.commit()


def _decoy_valve(pen: _Pen, x: float, y: float, r: float) -> None:
    """**弁**(配管の記号)。電気の凡例には無い。"""
    pen.line(x - r * 2, y, x + r * 2, y)
    pen.polyline([(x - r, y - r), (x - r, y + r), (x, y), (x - r, y - r)])
    pen.polyline([(x + r, y - r), (x + r, y + r), (x, y), (x + r, y - r)])


def _decoy_check_valve(pen: _Pen, x: float, y: float, r: float) -> None:
    """**逆止弁**。弁に閉じ板を足した形。配管の記号。"""
    _decoy_valve(pen, x, y, r)
    pen.line(x + r, y - r, x + r, y + r)


def _decoy_expansion(pen: _Pen, x: float, y: float, r: float) -> None:
    """**伸縮継手**。配管の記号。"""
    pen.line(x - r * 2, y, x - r, y)
    pen.line(x + r, y, x + r * 2, y)
    pen.rect(x - r, y - r, x + r, y + r)
    pen.polyline([(x - r, y + r), (x, y - r), (x, y + r), (x + r, y - r)])


def _decoy_gauge(pen: _Pen, x: float, y: float, r: float) -> None:
    """**圧力計**。配管の記号。"""
    pen.circle(x, y + r * 0.2, r)
    pen.line(x, y + r * 1.2, x, y + r * 2)
    pen.line(x - r * 0.6, y + r * 2, x + r * 0.6, y + r * 2)


def _decoy_crossed_bar(pen: _Pen, x: float, y: float, r: float) -> None:
    """丸に × の形に、**横棒を 1 本足した**もの。実在の描き方の変形。"""
    pen.circle(x, y, r)
    pen.line(x - r * 0.7, y - r * 0.7, x + r * 0.7, y + r * 0.7)
    pen.line(x + r * 0.7, y - r * 0.7, x - r * 0.7, y + r * 0.7)
    pen.line(x - r * 1.4, y, x + r * 1.4, y)


def _decoy_two_dots(pen: _Pen, x: float, y: float, r: float) -> None:
    """丸の中に**小さな丸を 2 つ**。中の文字を丸に置き換えた変形。"""
    pen.circle(x, y, r)
    pen.circle(x - r * 0.4, y, r * 0.25)
    pen.circle(x + r * 0.4, y, r * 0.25)


def _decoy_double_diagonal(pen: _Pen, x: float, y: float, r: float) -> None:
    """四角に斜め線 1 本の形を、**2 本に変えて外に印**を足したもの。"""
    pen.rect(x - r, y - r * 0.7, x + r, y + r * 0.7)
    pen.line(x - r, y + r * 0.7, x + r, y - r * 0.7)
    pen.line(x - r, y - r * 0.7, x + r, y + r * 0.7)
    pen.line(x + r, y - r * 1.1, x + r * 1.4, y - r * 1.1)


def _decoy_barred_circle(pen: _Pen, x: float, y: float, r: float) -> None:
    """丸に縦棒 1 本。似た形だが、この凡例の意味とは別。"""
    pen.circle(x, y, r)
    pen.line(x, y - r, x, y + r)


def _decoy_triangle_in_circle(pen: _Pen, x: float, y: float, r: float) -> None:
    """丸の中に三角。似た形だが、この凡例の意味とは別。"""
    pen.circle(x, y, r)
    pen.polyline(
        [(x, y - r * 0.6), (x + r * 0.6, y + r * 0.5), (x - r * 0.6, y + r * 0.5), (x, y - r * 0.6)]
    )


def _decoy_nested_square(pen: _Pen, x: float, y: float, r: float) -> None:
    """二重の四角に点。似た形だが、この凡例の意味とは別。"""
    pen.rect(x - r, y - r * 0.8, x + r, y + r * 0.8)
    pen.rect(x - r * 0.5, y - r * 0.4, x + r * 0.5, y + r * 0.4)
    pen.circle(x, y, r * 0.12)


#: 囮の作り方。**正解はすべて「凡例に無い」。**3 種類に分けてある。
DECOY_RECIPES: tuple[tuple[str, Any], ...] = (
    ("囮 別の規格の記号", _decoy_valve),
    ("囮 別の規格の記号", _decoy_check_valve),
    ("囮 別の規格の記号", _decoy_expansion),
    ("囮 別の規格の記号", _decoy_gauge),
    ("囮 実在の記号の変形", _decoy_crossed_bar),
    ("囮 実在の記号の変形", _decoy_two_dots),
    ("囮 実在の記号の変形", _decoy_double_diagonal),
    ("囮 似た形で意味が違う", _decoy_barred_circle),
    ("囮 似た形で意味が違う", _decoy_triangle_in_circle),
    ("囮 似た形で意味が違う", _decoy_nested_square),
)


def _really_empty(
    pdf_path: Path | str, page_number: int, cells: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """**本当に何も無い升目**だけを残す。

    ``blank_cells`` は**文字が無いこと**しか見ていない。K-22 (c) で描いてみたら、
    文字は無いが**写真が貼ってある升目**が選ばれた。囮をその上に描くことになるので、
    絵も写真も無いことを確かめる。
    """
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_number - 1]
        drawings = page_drawings(page)
        matrix = page.rotation_matrix
        images = [
            pymupdf.Rect(block["bbox"]) * matrix
            for block in page.get_text("dict")["blocks"]
            if block.get("type") == 1
        ]
    kept: list[dict[str, Any]] = []
    for cell in cells:
        box = pymupdf.Rect(*cell["rect"])
        own, _ = _own_and_foreign(box, drawings)
        if not own.is_empty:
            continue
        if any(image.intersects(box) for image in images):
            continue
        kept.append(cell)
    return kept


def decoys_in_cells(
    pdf_path: Path | str,
    page_number: int,
    count: int = len(DECOY_RECIPES),
    seed: int = SHUFFLE_SEED,
    zoom: float = CROP_ZOOM,
) -> list[tuple[dict[str, Any], pymupdf.Pixmap]]:
    """**凡例の紙の空の升目に囮を描いて**切り出す。

    K-22 (c)。K-21 の囮は別の紙に描いたので、**線が太く、表の罫線が写っていない**
    ことで見破られた。紙の升目に描けば、枠・線の太さ・大きさ・かすれ方まで本物と同じになる。
    **元の PDF は書き換えない**(開き直した写しに描く)。
    """
    cells = blank_cells(pdf_path, page_number, wanted=max(count * 8, count))
    cells = _really_empty(pdf_path, page_number, cells)
    if not cells:
        return []
    chosen = random.Random(seed).sample(cells, min(count, len(cells)))
    made: list[tuple[dict[str, Any], pymupdf.Pixmap]] = []
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_number - 1]
        for index, cell in enumerate(chosen):
            kind, draw = DECOY_RECIPES[index % len(DECOY_RECIPES)]
            box = pymupdf.Rect(*cell["rect"])
            radius = min(box.height * 0.3, box.width * 0.12)
            pen = _Pen(page)
            draw(pen, (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, radius)
            pen.commit()
            made.append(
                (
                    {
                        "kind": kind,
                        "name": None,
                        "code": "",
                        "group": None,
                        "table_index": cell["table_index"],
                        "row_index": cell["row_index"],
                        "rect": cell["rect"],
                    },
                    None,
                )
            )
        drawings = page_drawings(page)
        words = page_words(page)
        made = [
            (entry, crop(page, entry["rect"], drawings, zoom=zoom, words=words))
            for entry, _ in made
        ]
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--page", type=int, default=22, help="「名称 / 記号」の表があるページ")
    parser.add_argument("--sheets", required=True, type=Path, help="紙を置く作業用フォルダ")
    parser.add_argument(
        "--answers", required=True, type=Path, help="正解の対応表。**共有フォルダに置くこと**"
    )
    parser.add_argument("--seed", type=int, default=SHUFFLE_SEED)
    args = parser.parse_args(argv)

    rows = name_rows(args.pdf, args.page)
    decoys = blank_cells(args.pdf, args.page, BLANK_DECOYS)
    entries: list[dict[str, Any]] = rows + decoys

    with pymupdf.open(args.pdf) as doc:
        page = doc[args.page - 1]
        drawings = page_drawings(page)
        words = page_words(page)
        pixmaps = [crop(page, entry["rect"], drawings, words=words) for entry in entries]
    for entry, pixmap in drawn_decoys(DRAWN_DECOYS):
        entries.append(entry)
        pixmaps.append(pixmap)

    order = list(range(len(entries)))
    random.Random(args.seed).shuffle(order)
    labelled = [(f"S-{position + 1:03d}", order[position]) for position in range(len(order))]

    sheets = build_sheets([(label, pixmaps[index]) for label, index in labelled], args.sheets)

    answers = [
        {
            "id": label,
            "kind": entries[index]["kind"],
            "name": entries[index]["name"],
            "code": entries[index]["code"],
            "group": entries[index]["group"],
            "table_index": entries[index]["table_index"],
            "row_index": entries[index]["row_index"],
        }
        for label, index in labelled
    ]
    args.answers.parent.mkdir(parents=True, exist_ok=True)
    args.answers.write_text(
        json.dumps({"seed": args.seed, "answers": answers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counted: dict[str, int] = {}
    for entry in entries:
        counted[entry["kind"]] = counted.get(entry["kind"], 0) + 1
    print(f"切り出した升目: {len(entries)} 件")
    for kind in sorted(counted):
        print(f"  {kind}: {counted[kind]} 件")
    print(f"紙: {len(sheets)} 枚 -> {args.sheets}")
    print(f"正解の対応表 -> {args.answers}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
