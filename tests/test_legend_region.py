"""`axes/image_axis/legend_region.py` の回帰テスト(11周目)。

守りたいのは 3 つ。

1. **表の外にある図形からは、凡例の対応を 1 件も作らない。**
   図面のページを丸ごと凡例として読むと、図面の上でたまたま文字の右に
   図形が並んでいるだけの並びまで凡例になる(10 周目に 296 件)。
2. **同じ行に文字が無い図形には名前を付けない。** 名前を作らない。
3. **同じ近さの文字が 2 つあるときは決めない。** どちらかを黙って選ばない。

テスト用の PDF はその場で組み立てる(実図面はコミットしない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.legend_region import (
    METHOD_LEGEND_TABLE,
    read_legend_in_tables,
)
from axes.image_axis.pdf_tables import TableCell, TableRegion
from axes.image_axis.pdf_vector_symbols import DrawingScale

SCALE_50 = DrawingScale(denominator=50.0, source_text="1/50")

#: 図形を描く位置。1/50 の図面なので 20pt は実寸およそ 350mm で、
#: 記号の大きさの窓(30〜1500mm)の中に入る。
SIZE_PT = 20.0


def _page(tmp_path: Path, shapes: list[tuple[float, float]]) -> Path:
    """指定の位置に同じ形の図形を置いただけの 1 ページの PDF を作る。"""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    for x, y in shapes:
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(x, y, x + SIZE_PT, y + SIZE_PT))
        shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + SIZE_PT, y + SIZE_PT))
        shape.finish(width=0.5)
        shape.commit()
    path = tmp_path / "page.pdf"
    doc.save(path)
    doc.close()
    return path


def _cell(text: str, x0: float, y0: float, x1: float, y1: float, row: int, col: int):
    return TableCell(text=text, rect_pt=(x0, y0, x1, y1), row_index=row, col_index=col)


def _legend_table(rows: list[tuple[str, str]]) -> TableRegion:
    """左に図形の欄、右に名前の欄がある表を手で組む。

    図形の欄は 100〜140pt、名前の欄は 140〜300pt。1 行の高さは 40pt。
    """
    built = []
    for index, (symbol_text, name) in enumerate(rows):
        top = 100.0 + index * 40.0
        built.append(
            (
                _cell(symbol_text, 100.0, top, 140.0, top + 40.0, index, 0),
                _cell(name, 140.0, top, 300.0, top + 40.0, index, 1),
            )
        )
    return TableRegion(page_index=0, rect_pt=(100.0, 100.0, 300.0, 100.0 + 40.0 * len(rows)), rows=tuple(built))


def test_表の升目の中の図形に名前が付く(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert [s.name for s in result.symbols] == ["コンセント"]


def test_手法IDが別になっている(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[_legend_table([("", "スイッチ")])])
    assert result.symbols[0].method_id == METHOD_LEGEND_TABLE
    assert METHOD_LEGEND_TABLE == "pdf_table_legend_symbol"


def test_表の外にある図形からは対応を作らない(tmp_path):
    """**これがこの周の要点。** 図面の上の図形を凡例にしない。"""
    pdf = _page(tmp_path, [(400.0, 300.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.symbols == ()
    assert result.dropped_outside_tables == 1


def test_同じ行に文字が無ければ名前を付けない(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    table = _legend_table([("", "")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.symbols == ()
    assert result.dropped_no_text_in_row == 1


def test_同じ近さの文字が2つあれば決めない(tmp_path):
    """左右に同じ距離で文字があるとき、**どちらかを黙って選ばない。**"""
    pdf = _page(tmp_path, [(150.0, 105.0)])
    row = (
        _cell("左の名前", 100.0, 100.0, 140.0, 140.0, 0, 0),
        _cell("", 140.0, 100.0, 180.0, 140.0, 0, 1),
        _cell("右の名前", 180.0, 100.0, 300.0, 140.0, 0, 2),
    )
    table = TableRegion(page_index=0, rect_pt=(100.0, 100.0, 300.0, 140.0), rows=(row,))
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.symbols == ()
    assert result.dropped_ambiguous == 1


def test_図形の入っているセル自身の文字は名前にしない(tmp_path):
    """図形の欄に文字が書いてあっても、それは名前ではない。"""
    pdf = _page(tmp_path, [(110.0, 105.0)])
    table = _legend_table([("この欄は図形", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert [s.name for s in result.symbols] == ["コンセント"]


def test_表が1つも無ければ対応は0件(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[])
    assert result.symbols == ()
    assert result.dropped_outside_tables == 1


def test_図形が1つも無ければ対応は0件(tmp_path):
    pdf = _page(tmp_path, [])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[_legend_table([("", "コンセント")])])
    assert result.symbols == ()


def test_表が入れ子のときは小さいほうの行を使う(tmp_path):
    """大きい表を採ると、**行が表全体の1行になり無関係な文字が同じ行に入る。**"""
    pdf = _page(tmp_path, [(110.0, 105.0)])
    small = _legend_table([("", "正しい名前")])
    big_row = (
        _cell("", 50.0, 50.0, 350.0, 350.0, 0, 0),
        _cell("無関係な文字", 350.0, 50.0, 550.0, 350.0, 0, 1),
    )
    big = TableRegion(page_index=0, rect_pt=(50.0, 50.0, 550.0, 350.0), rows=(big_row,))
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[big, small])
    assert [s.name for s in result.symbols] == ["正しい名前"]


def test_大きさの窓から外れた図形は読まない(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table], min_mm=5000.0, max_mm=9000.0)
    assert result.symbols == ()


def test_大きさの窓が不正なら断る(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    with pytest.raises(ValueError):
        read_legend_in_tables(pdf, 0, SCALE_50, tables=[], min_mm=100.0, max_mm=50.0)


def test_落とした件数が出力に残る(tmp_path):
    """**「対応が0件」で終わらせない。** なぜ0件なのかを数で残す。"""
    pdf = _page(tmp_path, [(110.0, 105.0), (400.0, 300.0)])
    table = _legend_table([("", "")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.dropped_no_text_in_row == 1
    assert result.dropped_outside_tables == 1
    assert "落とした" in result.summary()


def test_同じ入力なら同じ結果になる(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0), (110.0, 145.0)])
    table = _legend_table([("", "コンセント"), ("", "スイッチ")])
    first = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    second = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert [s.name for s in first.symbols] == [s.name for s in second.symbols]


def test_手法は未校正で上限はweak():
    """**凡例が読めても、名前が正しいことの校正にはならない。**

    ここを校正済みにすると、凡例から付いた名前が階層1(自動確定)の根拠になる。
    名前の突き合わせは表記のゆれが未着手で、正しさを一度も測っていない。
    """
    from arbitration.method_policies import DEFAULT_METHOD_POLICIES

    policy = DEFAULT_METHOD_POLICIES[METHOD_LEGEND_TABLE]
    assert policy.calibrated is False
    assert policy.max_strength == "weak"


# --- 12周目: 凡例の行は「1行に図形が1個」 -----------------------------------


def test_1行に図形が2個ある行は丸ごと落とす(tmp_path):
    """**この周の要点。** 図面が表に化けた行(1行に図形2,429個)を落とす。"""
    pdf = _page(tmp_path, [(105.0, 105.0), (112.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.symbols == ()
    assert result.dropped_crowded_rows == 1
    assert result.dropped_in_crowded_rows == 2


def test_図形が1個の行だけが残る(tmp_path):
    """混ざっていても、図形が1個の行だけが凡例になる。"""
    pdf = _page(tmp_path, [(110.0, 105.0), (105.0, 145.0), (112.0, 145.0)])
    table = _legend_table([("", "コンセント"), ("", "スイッチ")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert [s.name for s in result.symbols] == ["コンセント"]
    assert result.dropped_crowded_rows == 1


def test_条件を外すと11周目の振る舞いに戻る(tmp_path):
    """**対照として並べて測るために残してある。** 本番では既定のまま使う。"""
    pdf = _page(tmp_path, [(105.0, 105.0), (112.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table], max_shapes_per_row=None)
    assert len(result.symbols) == 2
    assert result.dropped_crowded_rows == 0


def test_1行に許す図形の数は1以上でなければ断る(tmp_path):
    pdf = _page(tmp_path, [(110.0, 105.0)])
    with pytest.raises(ValueError):
        read_legend_in_tables(pdf, 0, SCALE_50, tables=[], max_shapes_per_row=0)


def test_落とした行と図形の数が出力に残る(tmp_path):
    """**「対応が0件」で終わらせない。** 何行・何個落としたかを数で残す。"""
    pdf = _page(tmp_path, [(105.0, 105.0), (112.0, 105.0), (118.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table])
    assert result.dropped_in_crowded_rows == 3
    assert "図形が多すぎる行" in result.summary()


def test_許す数を増やせばその行も読む(tmp_path):
    """1行に記号を2つ並べた凡例は既定では落ちる。**落ちたことを「無い」と読まない。**"""
    pdf = _page(tmp_path, [(105.0, 105.0), (112.0, 105.0)])
    table = _legend_table([("", "コンセント")])
    result = read_legend_in_tables(pdf, 0, SCALE_50, tables=[table], max_shapes_per_row=2)
    assert len(result.symbols) == 2
