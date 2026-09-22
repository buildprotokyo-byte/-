"""`axes/image_axis/pdf_tables.py` の回帰テスト。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**
実案件の図面は匿名化済みであってもリポジトリに置かない決まりなので、
表の見本を図面から切り出してくることはしない。

守りたいのは 5 つ。

1. 罫線で組まれた表を、**セルごとの位置つき**で取り出せること。
   位置が落ちると、後から人が図面のどこを見ればよいのか分からなくなる。
2. **縦に結合されたセルと、単に空のセルを区別すること。** どちらも
   「その行に文字が無い」だが、前者は上の行の続き、後者は記入漏れか
   別の意味である。同じものとして扱うと、結合の解釈を後から覆せない。
3. **表の見出し(キャプション)を、表の上にある文字から拾うこと。**
   建具表と内装仕上表は、列見出しだけでは見分けがつかない図面がある。
4. **スキャンしただけのページからは何も返さないこと。** 文字が
   入っていないページで「表が 0 個」と「表が読めない」を取り違えない。
5. **セルの文字は図面に印字されたまま返すこと。** 空白の詰めや
   単位の解釈はこの層では一切しない。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_tables import find_tables


# ---------------------------------------------------------------------------
# 合成の表入り PDF を組み立てる道具
# ---------------------------------------------------------------------------


def draw_table(
    page: pymupdf.Page,
    *,
    origin: tuple[float, float],
    col_widths: tuple[float, ...],
    row_height: float,
    rows: tuple[tuple[str | None, ...], ...],
    caption: str | None = None,
    fontsize: float = 9.0,
) -> tuple[float, float, float, float]:
    """罫線つきの表を描く。

    セルの値が ``None`` なら、**そのセルの上側の横罫を引かない**
    (= 上のセルと縦に結合されている)。値が ``""`` なら罫線は引くが
    文字を書かない(= 空のセル)。実図面の内装仕上表にはどちらもある。

    返り値は表の外形 (x0, y0, x1, y1)。
    """
    x0, y0 = origin
    xs = [x0]
    for width in col_widths:
        xs.append(xs[-1] + width)
    ys = [y0 + index * row_height for index in range(len(rows) + 1)]

    if caption is not None:
        page.insert_text(
            pymupdf.Point(x0, y0 - 6.0), caption, fontname="japan", fontsize=fontsize + 2
        )

    shape = page.new_shape()
    for x in xs:
        shape.draw_line(pymupdf.Point(x, ys[0]), pymupdf.Point(x, ys[-1]))
    # 横罫は、結合されているセルの上では引かない。
    for row_index in range(len(rows) + 1):
        y = ys[row_index]
        for col_index in range(len(col_widths)):
            if 0 < row_index < len(rows) and rows[row_index][col_index] is None:
                continue
            shape.draw_line(
                pymupdf.Point(xs[col_index], y), pymupdf.Point(xs[col_index + 1], y)
            )
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()

    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            if not value:
                continue
            page.insert_text(
                pymupdf.Point(xs[col_index] + 3.0, ys[row_index] + row_height - 6.0),
                value,
                fontname="japan",
                fontsize=fontsize,
            )
    return (xs[0], ys[0], xs[-1], ys[-1])


def single_table_pdf(
    path: Path,
    rows: tuple[tuple[str | None, ...], ...],
    *,
    caption: str | None = None,
    col_widths: tuple[float, ...] | None = None,
) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    widths = col_widths or tuple(90.0 for _ in rows[0])
    draw_table(
        page,
        origin=(60.0, 120.0),
        col_widths=widths,
        row_height=24.0,
        rows=rows,
        caption=caption,
    )
    doc.save(path)
    doc.close()
    return path


def scanned_pdf(path: Path) -> Path:
    """線も文字も図形データとして持たない PDF(スキャン相当)。"""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, 10, 10, bytes([255] * 100), False)
    page.insert_image(pymupdf.Rect(0, 0, 842, 595), pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


DOOR_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("建具番号", "種別", "幅", "高さ", "数量"),
    ("WD-01", "引戸", "1650", "2000", "2"),
    ("WD-02", "折戸", "1200", "2000", "1"),
)


# ---------------------------------------------------------------------------
# 1. セルごとの位置
# ---------------------------------------------------------------------------


def test_a_ruled_table_is_read_cell_by_cell(tmp_path: Path) -> None:
    tables = find_tables(single_table_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)

    assert len(tables) == 1
    table = tables[0]
    assert table.page_index == 0
    assert table.texts() == [list(row) for row in DOOR_ROWS]


def test_every_cell_carries_its_position(tmp_path: Path) -> None:
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)[0]

    for row in table.rows:
        for cell in row:
            x0, y0, x1, y1 = cell.rect_pt
            assert x1 > x0 and y1 > y0
            # セルは表の外形の中にある。
            assert table.rect_pt[0] - 0.5 <= x0
            assert x1 <= table.rect_pt[2] + 0.5

    # 同じ行のセルは左から右へ、同じ列のセルは上から下へ並んでいる。
    first_row = table.rows[0]
    assert [cell.rect_pt[0] for cell in first_row] == sorted(
        cell.rect_pt[0] for cell in first_row
    )
    first_column = [row[0] for row in table.rows]
    assert [cell.rect_pt[1] for cell in first_column] == sorted(
        cell.rect_pt[1] for cell in first_column
    )


def test_the_cell_text_is_kept_as_printed(tmp_path: Path) -> None:
    rows = (("記号", "備考"), ("AW-1", "既存 撤去"))
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", rows), 0)[0]

    assert table.rows[1][1].text == "既存 撤去"


# ---------------------------------------------------------------------------
# 2. 結合されたセルと空のセル
# ---------------------------------------------------------------------------


MERGED_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室1", "床", "フローリング"),
    (None, "壁", "ビニルクロス"),
    ("浴室", "床", "タイル"),
    ("", "壁", "タイル"),
)


def test_a_vertically_merged_cell_is_marked_as_merged(tmp_path: Path) -> None:
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", MERGED_ROWS), 0)[0]

    merged = table.rows[2][0]
    assert merged.merged_with_above is True
    assert merged.text == ""


def test_an_empty_cell_is_not_reported_as_merged(tmp_path: Path) -> None:
    """罫線で区切られているのに文字が無いセルは、結合ではなく空。"""
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", MERGED_ROWS), 0)[0]

    blank = table.rows[4][0]
    assert blank.merged_with_above is False
    assert blank.text == ""


def test_a_merged_cell_keeps_the_position_of_the_cell_it_belongs_to(
    tmp_path: Path,
) -> None:
    """結合されたセルの位置は、文字が書かれている親セルの位置にする。

    その行の座標を返すと、人が図面を見に行ったときに空白を指すことになる。
    """
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", MERGED_ROWS), 0)[0]

    parent = table.rows[1][0]
    merged = table.rows[2][0]
    assert merged.rect_pt == parent.rect_pt


# ---------------------------------------------------------------------------
# 3. 見出し(キャプション)
# ---------------------------------------------------------------------------


def test_the_caption_above_the_table_is_picked_up(tmp_path: Path) -> None:
    table = find_tables(
        single_table_pdf(tmp_path / "a.pdf", DOOR_ROWS, caption="建具表"), 0
    )[0]

    assert table.caption == "建具表"


def test_no_caption_is_invented_when_there_is_no_text_above(tmp_path: Path) -> None:
    table = find_tables(single_table_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)[0]

    assert table.caption is None


def test_text_far_above_the_table_is_not_taken_as_its_caption(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    # 図面の表題欄のように、表からずっと離れた位置にある文字。
    page.insert_text(pymupdf.Point(60.0, 40.0), "建具表", fontname="japan", fontsize=11)
    draw_table(
        page,
        origin=(60.0, 300.0),
        col_widths=(90.0,) * 5,
        row_height=24.0,
        rows=DOOR_ROWS,
    )
    path = tmp_path / "a.pdf"
    doc.save(path)
    doc.close()

    assert find_tables(path, 0)[0].caption is None


# ---------------------------------------------------------------------------
# 4. 表が無いページ・読めないページ
# ---------------------------------------------------------------------------


def test_a_scanned_page_yields_no_tables(tmp_path: Path) -> None:
    assert find_tables(scanned_pdf(tmp_path / "a.pdf"), 0) == []


def test_a_plain_drawing_page_yields_no_tables(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(100, 100), pymupdf.Point(400, 100))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    page.insert_text(pymupdf.Point(100, 500), "1/50", fontsize=10)
    path = tmp_path / "a.pdf"
    doc.save(path)
    doc.close()

    assert find_tables(path, 0) == []


def test_an_out_of_range_page_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    path = single_table_pdf(tmp_path / "a.pdf", DOOR_ROWS)
    with pytest.raises(IndexError):
        find_tables(path, 7)


# ---------------------------------------------------------------------------
# 5. 複数の表
# ---------------------------------------------------------------------------


def test_two_tables_on_one_page_are_returned_separately(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    draw_table(
        page,
        origin=(60.0, 120.0),
        col_widths=(90.0,) * 5,
        row_height=24.0,
        rows=DOOR_ROWS,
        caption="建具表",
    )
    draw_table(
        page,
        origin=(60.0, 320.0),
        col_widths=(110.0, 80.0, 150.0),
        row_height=24.0,
        rows=MERGED_ROWS,
        caption="内装仕上表",
    )
    path = tmp_path / "a.pdf"
    doc.save(path)
    doc.close()

    tables = find_tables(path, 0)
    assert len(tables) == 2
    assert [table.caption for table in tables] == ["建具表", "内装仕上表"]
    # 上にあるものが先に来る(呼び出し順に依存しない)。
    assert tables[0].rect_pt[1] < tables[1].rect_pt[1]
