"""画像軸: CAD 由来の PDF から、罫線で組まれた表を**位置つき**で取り出す。

なぜ要るのか
------------
2026-09-22 時点で、このリポジトリが PDF の文字から読めるのは
`axes/image_axis/pdf_vector_symbols.find_area_labels()` が拾う面積の記載
2 種類だけだった。図面の文字を読む経路は `page.get_text("text")` を
正規表現で舐めるもので、**行と列の対応が失われる**。表題欄の面積のように
「ラベルと数値が隣り合う」だけの記載はそれで足りるが、建具表や内装仕上表は
1 つの値の意味が**同じ行の別の列**(建具番号)と**同じ列の見出し**(幅・数量)
の両方で決まるので、平文にした時点で読めなくなる。

このモジュールは、表を**セルの升目**として取り出し、それぞれに
**ページ上の座標**を付けて返す。意味づけ(どの列が幅か、この表は建具表か)は
ここではやらない。それは `axes/image_axis/schedule_tables.py` の仕事である。

このモジュールが守ること
------------------------
1. **セルの文字は図面に印字されたまま返す。** 空白の詰め、単位の解釈、
   全角半角の寄せはしない。読み手が後から元の文字列に戻れなくなる。
2. **縦に結合されたセルと、罫線はあるが空のセルを区別する。** 内装仕上表は
   室名を縦に結合して書くのが普通で、そこを「空」と扱うと室名が消える。
   逆に、単に記入が無いセルを「上の続き」と扱うと、書かれていない仕上げを
   図面が指定したことにしてしまう。この 2 つは
   `TableCell.merged_with_above` で区別できる。
3. **推測しない。** 罫線が無い表(文字の並びだけで組まれた表)はここでは
   拾わない。座標の近さだけで列を作ると、図面の中の寸法文字の並びを
   表と取り違える。拾えなかったことは「表 0 個」として返る。
4. **スキャンしたページからは何も返らない。** 文字も罫線も図形データとして
   入っていないため。**0 個であることは「表が無い」ではない。**

罫線の検出そのものは PyMuPDF の `Page.find_tables()` に任せている。
自前で罫線を探して升目を組み直す実装は書いていない。理由は 2 つで、
(a) 罫線の途切れ・二重線・セル内の塗りといった実図面の癖に対する調整が
既に入っていること、(b) このリポジトリで新しく書いた検出器は、実図面で
**的中 0 件**だった前例(ラスターのテンプレート照合)があること。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

#: キャプション(表の見出し)とみなす、表の上端からの最大の隙間(ポイント)。
#:
#: 実図面の表は見出しをすぐ上に置くが、図面の表題欄や凡例はずっと離れている。
#: 24pt は 9〜11pt の文字でおよそ 2 行ぶんで、「表のすぐ上」と
#: 「ページのどこかにある文字」を分ける値として選んだ。**実測で校正した値では
#: ない。** 離れた文字を見出しにしてしまうより、見出しが取れないほうが安全な
#: 向きに倒してある(見出しが None でも、列見出しから表の種類は判定できる)。
CAPTION_MAX_GAP_PT = 24.0

#: キャプションとみなすには、表の横幅とこれだけ重なっている必要がある(比)。
#: 表の真上にある文字だけを拾い、隣の欄の文字を拾わないための条件。
CAPTION_MIN_OVERLAP = 0.2


@dataclass(frozen=True)
class TableCell:
    """表の升目 1 つ。"""

    text: str
    """図面に印字されたまま。空のセルと結合されたセルはどちらも ``""``。"""

    rect_pt: tuple[float, float, float, float]
    """ページ座標(ポイント)の外接矩形。

    結合されたセルでは、**文字が書かれている親セルの矩形**を返す。
    その行の座標を返すと、人が図面を見に行ったときに空白を指すことになる。
    """

    row_index: int
    col_index: int

    merged_with_above: bool = False
    """上のセルと縦に結合されている(= 罫線がそこで途切れている)。

    ``False`` で ``text`` が空なら、罫線はあるが記入が無いセルである。
    **この 2 つを同じものとして扱わないこと。**
    """


@dataclass(frozen=True)
class TableRegion:
    """1 ページの中の表 1 つ。"""

    page_index: int
    """0 始まり。"""

    rect_pt: tuple[float, float, float, float]
    """表の外形(ページ座標・ポイント)。"""

    rows: tuple[tuple[TableCell, ...], ...]
    """上から順の行。各行は左から順のセル。"""

    caption: str | None = None
    """表のすぐ上にある文字。無ければ None。**空文字で埋めない。**"""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def col_count(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def texts(self) -> list[list[str]]:
        """文字だけを取り出す。位置が要らない比較・表示のため。"""
        return [[cell.text for cell in row] for row in self.rows]

    def header_texts(self) -> tuple[str, ...]:
        """1 行目の文字。**1 行目が見出しである保証はここには無い。**"""
        return tuple(cell.text for cell in self.rows[0]) if self.rows else ()


def find_tables(pdf_path: str | Path, page_index: int) -> list[TableRegion]:
    """ページの中の罫線表を、上にあるものから順に返す。

    表が 1 つも無ければ空のリスト。スキャンしたページでも空になるが、
    **それは「表が無い」ことの証明ではない**(このモジュール冒頭 4)。
    """
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"ページ {page_index} は存在しません(全 {doc.page_count} ページ)")
        page = doc.load_page(page_index)
        found = page.find_tables()
        regions = [
            _to_region(table, page_index) for table in found.tables
        ]
        regions = [region for region in regions if region.row_count]
        regions.sort(key=lambda region: (round(region.rect_pt[1], 1), round(region.rect_pt[0], 1)))
        occupied = tuple(region.rect_pt for region in regions)
        lines = _text_lines(page)
        return [
            _with_caption(region, lines, occupied) for region in regions
        ]


# ---------------------------------------------------------------------------
# 升目を組み立てる
# ---------------------------------------------------------------------------


def _to_region(table, page_index: int) -> TableRegion:
    """PyMuPDF の表を `TableRegion` に移す。結合セルはここで解く。"""
    values = table.extract()
    boxes = [list(row.cells) for row in table.rows]
    col_count = max((len(row) for row in boxes), default=0)

    rows: list[tuple[TableCell, ...]] = []
    #: 列ごとに、直前に見た「実体のあるセル」(結合の親)を覚えておく。
    parents: dict[int, TableCell] = {}
    for row_index, box_row in enumerate(boxes):
        cells: list[TableCell] = []
        for col_index in range(col_count):
            box = box_row[col_index] if col_index < len(box_row) else None
            raw = (
                values[row_index][col_index]
                if row_index < len(values) and col_index < len(values[row_index])
                else None
            )
            if box is None:
                # PyMuPDF は「上のセルに飲み込まれた升目」を None で返す。
                # 罫線で区切られたうえで空なら "" が返るので、両者は分かれる。
                parent = parents.get(col_index)
                cell = TableCell(
                    text="",
                    rect_pt=parent.rect_pt if parent is not None
                    else _fallback_rect(boxes, row_index, col_index, table),
                    row_index=row_index,
                    col_index=col_index,
                    merged_with_above=True,
                )
            else:
                cell = TableCell(
                    text=raw if raw is not None else "",
                    rect_pt=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                    row_index=row_index,
                    col_index=col_index,
                    merged_with_above=False,
                )
                parents[col_index] = cell
            cells.append(cell)
        rows.append(tuple(cells))

    rect = table.bbox
    return TableRegion(
        page_index=page_index,
        rect_pt=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
        rows=tuple(rows),
    )


def _fallback_rect(
    boxes: list[list], row_index: int, col_index: int, table
) -> tuple[float, float, float, float]:
    """結合の親が上に見つからないとき(表の 1 行目が結合されている等)の矩形。

    その列のどこかにある升目から横方向の範囲を、同じ行のどこかにある升目から
    縦方向の範囲を借りる。**どちらも無ければ表の外形を返す。**
    座標を 0 や原点で埋めると、図面上の別の場所を指すことになる。
    """
    xs = [
        (float(row[col_index][0]), float(row[col_index][2]))
        for row in boxes
        if col_index < len(row) and row[col_index] is not None
    ]
    ys = [
        (float(box[1]), float(box[3]))
        for box in boxes[row_index]
        if box is not None
    ]
    rect = table.bbox
    x0, x1 = xs[0] if xs else (float(rect[0]), float(rect[2]))
    y0, y1 = ys[0] if ys else (float(rect[1]), float(rect[3]))
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# 見出し(キャプション)
# ---------------------------------------------------------------------------


def _text_lines(page: pymupdf.Page) -> list[tuple[tuple[float, float, float, float], str]]:
    """ページの文字を、行ごとの (矩形, 文字列) にして返す。"""
    out: list[tuple[tuple[float, float, float, float], str]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", ()):
        for line in block.get("lines", ()):
            text = "".join(span.get("text", "") for span in line.get("spans", ())).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            out.append(((float(x0), float(y0), float(x1), float(y1)), text))
    return out


def _with_caption(
    region: TableRegion,
    lines: list[tuple[tuple[float, float, float, float], str]],
    occupied: tuple[tuple[float, float, float, float], ...],
) -> TableRegion:
    """表のすぐ上にある行を見出しとして付ける。無ければ None のまま。"""
    tx0, ty0, tx1, _ = region.rect_pt
    width = max(tx1 - tx0, 1e-6)
    best: tuple[float, str] | None = None
    for (x0, y0, x1, y1), text in lines:
        gap = ty0 - y1
        if not 0.0 <= gap <= CAPTION_MAX_GAP_PT:
            continue
        overlap = min(x1, tx1) - max(x0, tx0)
        if overlap / width < CAPTION_MIN_OVERLAP and overlap <= 0:
            continue
        if any(_inside((x0, y0, x1, y1), rect) for rect in occupied):
            # 別の表の中の文字。見出しではない。
            continue
        if best is None or gap < best[0]:
            best = (gap, text)
    if best is None:
        return region
    return TableRegion(
        page_index=region.page_index,
        rect_pt=region.rect_pt,
        rows=region.rows,
        caption=best[1],
    )


def _inside(
    inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]
) -> bool:
    return (
        inner[0] >= outer[0] - 1.0
        and inner[1] >= outer[1] - 1.0
        and inner[2] <= outer[2] + 1.0
        and inner[3] <= outer[3] + 1.0
    )
