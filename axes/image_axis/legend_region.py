"""凡例の「領域」を図面の中から切り出して、その中だけを凡例として読む。

2026-09-23、11周目。

なぜこれが要るのか
------------------
10 周目(`docs/a1_real_drawing_symbol_report.md`)で分かったのは、
**記号は数えられているが「どれが何か」が分からない**ということだった。
名前は凡例からしか来ない。ところが**この案件には凡例だけのページが無く、
凡例は電気の図面の中に描かれている。**

`pdf_repeated_symbols.read_legend_symbols()` はページを丸ごと凡例として読む。
図面のページに当てると、**図面の上でたまたま文字の右に図形が並んでいるだけの
並びまで凡例として読む**(22 ページで 296 件。凡例の項目がそんなにあるはずがない)。
`pdf_repeated_symbols.py` の docstring が警告しているとおりの誤読である。

何で凡例を選ぶのか
------------------
**語では選ばない。** セルの中の「凡例」「記号」の語で絞ると、
22 ページの 8 つの表のうち 6 つが当たる(11 周目の診断)。
語の有無で決めると、**語が無い凡例を落とし、語がある別の表を拾う。**

**形で選ぶ。** 凡例は「表の升目」という形をしている。
図形と名前が**同じ行の隣り合うセル**に入っていることを条件にする。

守ること
--------
1. **表の外にある図形からは、凡例の対応を 1 件も作らない。**
2. **同じ行に文字が無い図形には名前を付けない。** 名前を作らない。
3. **同じ行に文字の入ったセルが複数あっても、いちばん近いものを黙って選ばない。**
   近さが同じなら決めない(落とす)。
4. **凡例の行には図形が 1 個しか入らない**(12 周目に足した条件)。
   「記号」の欄に記号が 1 つ、「名称」の欄に名前が 1 つ、という並びだからである。
   **1 行に図形が 2 個以上ある行は、凡例の行ではない**ので丸ごと落とす。
   11 周目にこの条件が無かったとき、図面が表に化けた行(1 行に図形 2,429 個)が
   そのまま通り、**その行の図形が全部同じ名前になった。**
   1 行に記号を 2 つ並べた凡例はこの条件で落ちる。実在しうる書き方なので、
   **落ちたことを「凡例が無い」と読まない。** 落とした数は出力に残す。
5. **この手法も未校正である。** `arbitration/method_policies.py` に
   `calibrated=False` / 上限 `weak` で登録する。
   凡例が読めても、名前が正しいことの校正にはならない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axes.image_axis.pdf_repeated_symbols import (
    SYMBOL_MAX_MM,
    SYMBOL_MIN_MM,
    LegendSymbol,
    # 図形の記述を作る処理は 1 つしか無いほうがよい。**同じ記述で突き合わせないと
    # 群と凡例が一致しなくなる**ので、private だが同じパッケージ内で使い回す。
    _page_shapes,
)
from axes.image_axis.pdf_tables import TableRegion, find_tables
from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 表の升目から読んだ凡例の手法ID。ページ全体から読む
#: `pdf_vector_legend_symbol` とは**別の手法**にしてある。
#: 同じ ID にすると、領域を切り出したことの効き目が前の測定に混ざる。
METHOD_LEGEND_TABLE = "pdf_table_legend_symbol"


@dataclass(frozen=True)
class LegendReadResult:
    """凡例として読めた対応と、**読まなかったものの数**。"""

    symbols: tuple[LegendSymbol, ...] = ()
    tables_seen: int = 0
    shapes_in_tables: int = 0
    dropped_no_text_in_row: int = 0
    """同じ行に文字が無かったので名前を付けなかった図形の数。"""

    dropped_ambiguous: int = 0
    """同じ行に、同じ近さの文字が 2 つ以上あったので決めなかった図形の数。"""

    dropped_outside_tables: int = 0
    """表の外にあったので凡例として読まなかった図形の数。"""

    dropped_crowded_rows: int = 0
    """図形が 2 個以上あったので丸ごと落とした行の数。"""

    dropped_in_crowded_rows: int = 0
    """その行に入っていて落とした図形の数。**1 行に 2,429 個ということがある。**"""

    def summary(self) -> str:
        return (
            f"凡例として読めた対応 {len(self.symbols)} 件 / "
            f"表 {self.tables_seen} 個 / 表の中の図形 {self.shapes_in_tables} 個 / "
            f"落とした: 同じ行に文字が無い {self.dropped_no_text_in_row}・"
            f"決められない {self.dropped_ambiguous}・"
            f"表の外 {self.dropped_outside_tables}・"
            f"図形が多すぎる行 {self.dropped_crowded_rows}行({self.dropped_in_crowded_rows}個)"
        )


def read_legend_in_tables(
    pdf_path: str | Path,
    page_index: int,
    scale: DrawingScale,
    *,
    min_mm: float = SYMBOL_MIN_MM,
    max_mm: float = SYMBOL_MAX_MM,
    tables: list[TableRegion] | None = None,
    max_shapes_per_row: int | None = 1,
) -> LegendReadResult:
    """罫線の表の升目の中だけを凡例として読む。

    ``tables`` を渡さなければ `find_tables()` で取る
    (試験で表を手で組むために外から渡せるようにしてある)。

    ``max_shapes_per_row`` は 1 行に許す図形の数。既定の 1 が
    「凡例の行には図形が 1 個しか入らない」という条件である。
    ``None`` にすると 11 周目の振る舞い(条件なし)に戻る。
    **対照として並べて測るために残してある。本番では既定のまま使う。**
    """
    if min_mm <= 0 or max_mm <= min_mm:
        raise ValueError("大きさの窓が不正です")

    regions = find_tables(pdf_path, page_index) if tables is None else list(tables)
    shapes = [
        shape
        for shape in _page_shapes(pdf_path, page_index, scale)
        if min_mm <= shape.size_mm <= max_mm
    ]

    if max_shapes_per_row is not None and max_shapes_per_row < 1:
        raise ValueError("1 行に許す図形の数は 1 以上にしてください")

    symbols: list[LegendSymbol] = []
    no_text = ambiguous = outside = 0

    # **行ごとに数えてから読む。** 1 行に図形が 2 個以上あれば、その行は
    # 凡例の行ではない(図面が表に見えているだけ)ので丸ごと落とす。
    placed: list[tuple[object, tuple[object, int, int]]] = []
    for shape in shapes:
        cell = _cell_containing(regions, shape.center_pt)
        if cell is None:
            outside += 1
            continue
        placed.append((shape, cell))

    per_row: dict[tuple[int, int], int] = {}
    for _, (table, row_index, _col) in placed:
        per_row[(id(table), row_index)] = per_row.get((id(table), row_index), 0) + 1

    crowded = {key for key, count in per_row.items()
               if max_shapes_per_row is not None and count > max_shapes_per_row}
    crowded_shapes = sum(count for key, count in per_row.items() if key in crowded)

    for shape, (table, row_index, col_index) in placed:
        if (id(table), row_index) in crowded:
            continue
        name_cell = _name_cell_in_row(table, row_index, col_index)
        if name_cell is None:
            no_text += 1
            continue
        if name_cell == "決められない":
            ambiguous += 1
            continue
        symbols.append(
            LegendSymbol(
                name=name_cell.text,
                name_rect_pt=name_cell.rect_pt,
                symbol_rect_pt=shape.rect_pt,
                size_mm=shape.size_mm,
                page_index=page_index,
                descriptor=shape.descriptor,
                method_id=METHOD_LEGEND_TABLE,
            )
        )

    return LegendReadResult(
        symbols=tuple(symbols),
        tables_seen=len(regions),
        shapes_in_tables=len(shapes) - outside,
        dropped_no_text_in_row=no_text,
        dropped_ambiguous=ambiguous,
        dropped_outside_tables=outside,
        dropped_crowded_rows=len(crowded),
        dropped_in_crowded_rows=crowded_shapes,
    )


def _cell_containing(regions, point):
    """点を含む升目を返す。**表が重なっていたら小さいほうを採る。**

    大きい表の中に小さい表が入れ子で取れることがある。大きいほうを採ると、
    行が表全体の 1 行になり、**同じ行に無関係な文字が入る。**
    """
    best = None
    best_area = None
    for table in regions:
        for row_index, row in enumerate(table.rows):
            for col_index, cell in enumerate(row):
                if not _inside(cell.rect_pt, point):
                    continue
                x0, y0, x1, y1 = cell.rect_pt
                area = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
                if best_area is None or area < best_area:
                    best = (table, row_index, col_index)
                    best_area = area
    return best


def _name_cell_in_row(table: TableRegion, row_index: int, col_index: int):
    """同じ行の、文字の入ったいちばん近いセルを返す。

    - 見つからなければ ``None``(**名前を作らない**)。
    - 同じ近さのものが 2 つ以上あれば ``"決められない"``
      (**どちらかを黙って選ばない**)。
    - 図形が入っているセル自身は、そこに文字があっても名前にしない。
    """
    row = table.rows[row_index]
    candidates: list[tuple[int, object]] = []
    for index, cell in enumerate(row):
        if index == col_index or not cell.text.strip():
            continue
        candidates.append((abs(index - col_index), cell))
    if not candidates:
        return None
    nearest = min(distance for distance, _ in candidates)
    tied = [cell for distance, cell in candidates if distance == nearest]
    if len(tied) > 1:
        return "決められない"
    return tied[0]


def _inside(rect: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    x0, y0, x1, y1 = rect
    x, y = point
    return x0 <= x <= x1 and y0 <= y <= y1


__all__ = [
    "METHOD_LEGEND_TABLE",
    "LegendReadResult",
    "read_legend_in_tables",
]
