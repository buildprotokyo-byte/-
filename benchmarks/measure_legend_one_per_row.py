"""12周目の測定: 凡例の行は「1行に図形が1個」という条件の効き目。

`docs/a1_legend_one_per_row_criteria.md` の指標をそのまま測る。

**出すのは件数と割合だけ。** 記号の名前・位置・個数そのものは印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_legend_one_per_row --pdf <図面.pdf>
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from axes.image_axis.legend_region import read_legend_in_tables
from axes.image_axis.pdf_repeated_symbols import find_repeated_symbols, name_clusters
from axes.image_axis.pdf_tables import TableRegion, find_tables
from axes.image_axis.pdf_vector_symbols import extract_scale

#: 電気の図面のページ(0 始まり)。
TARGET_PAGES: tuple[int, ...] = (21, 22, 23)

#: 対照4 に使う、電気でない図面のページ(0 始まり)。
OTHER_DRAWING_PAGE = 32

#: 対照3 に使う、図形がほとんど無いページ(0 始まり)。
EMPTY_PAGE = 24


def name_shape_stats(result) -> tuple[int, float, int]:
    """(名前の数, 1 つの名前に付いた形の数の平均, その最大)。

    **凡例なら 1 つの名前に 1 つの形しか対応しない。**
    """
    by_name: dict[str, set] = defaultdict(set)
    for symbol in result.symbols:
        by_name[symbol.name].add(symbol.descriptor)
    if not by_name:
        return (0, 0.0, 0)
    counts = [len(shapes) for shapes in by_name.values()]
    return (len(by_name), sum(counts) / len(counts), max(counts))


def shuffled_tables(tables: list[TableRegion], seed: int) -> list[TableRegion]:
    """**行の中身をばらばらにする。** 行という単位を見ているかの対照。

    セルの位置(`rect_pt`)はそのままで、どのセルが同じ行かだけを入れ替える。
    行の単位を見ていれば、対応の数か「1 名前あたりの形の数」が変わる。
    """
    rng = random.Random(seed)
    out: list[TableRegion] = []
    for table in tables:
        cells = [cell for row in table.rows for cell in row]
        rng.shuffle(cells)
        widths = [len(row) for row in table.rows]
        rows = []
        cursor = 0
        for width in widths:
            rows.append(tuple(cells[cursor : cursor + width]))
            cursor += width
        out.append(TableRegion(page_index=table.page_index, rect_pt=table.rect_pt, rows=tuple(rows)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    print("対照1 11周目(条件なし)と並べる")
    for page in TARGET_PAGES:
        scale = extract_scale(args.pdf, page)
        if scale is None:
            print(f"  {page + 1} ページ: 縮尺が読めない")
            continue
        new = read_legend_in_tables(args.pdf, page, scale)
        old = read_legend_in_tables(args.pdf, page, scale, max_shapes_per_row=None)
        n_names, n_mean, n_max = name_shape_stats(new)
        o_names, o_mean, o_max = name_shape_stats(old)
        print(
            f"  {page + 1} ページ 条件なし: 対応 {len(old.symbols):5d} / 名前 {o_names:3d} / "
            f"1名前あたりの形 平均 {o_mean:6.2f} 最大 {o_max:4d}"
        )
        print(
            f"  {page + 1} ページ 1行1図形: 対応 {len(new.symbols):5d} / 名前 {n_names:3d} / "
            f"1名前あたりの形 平均 {n_mean:6.2f} 最大 {n_max:4d}"
        )
        print(f"    {new.summary()}")

    page = TARGET_PAGES[0]
    scale = extract_scale(args.pdf, page)
    counts = [len(read_legend_in_tables(args.pdf, page, scale).symbols) for _ in range(args.runs)]
    print()
    print(f"対照2 同じページを {args.runs} 回: {counts} → " + ("同じ" if len(set(counts)) == 1 else "**違う(欠陥)**"))

    empty_scale = extract_scale(args.pdf, EMPTY_PAGE)
    if empty_scale is None:
        print(
            f"対照3 {EMPTY_PAGE + 1} ページ: **縮尺が読めないので手法が動かない。**"
            "「表が無いから0件」とは別物なので合格として数えない"
        )
    else:
        empty = read_legend_in_tables(args.pdf, EMPTY_PAGE, empty_scale)
        print(f"対照3 {EMPTY_PAGE + 1} ページ(図形が少ない): 対応 {len(empty.symbols)} 件")

    legend = list(read_legend_in_tables(args.pdf, page, scale).symbols)
    for target, label in ((page, "本測定"), (OTHER_DRAWING_PAGE, "対照4 入れ替え(電気でない図面)")):
        target_scale = extract_scale(args.pdf, target)
        clusters = find_repeated_symbols(args.pdf, target, target_scale)
        named = [n for n in name_clusters(clusters, legend) if n.name]
        print(f"{label} {target + 1} ページ: 群 {len(clusters)} → 名前が付いた群 {len(named)}")

    tables = find_tables(args.pdf, page)
    shuffled = read_legend_in_tables(
        args.pdf, page, scale, tables=shuffled_tables(tables, seed=0)
    )
    s_names, s_mean, s_max = name_shape_stats(shuffled)
    print(
        f"対照5 行の中身をばらばらに: 対応 {len(shuffled.symbols)} / 名前 {s_names} / "
        f"1名前あたりの形 平均 {s_mean:.2f} 最大 {s_max}"
    )


if __name__ == "__main__":
    main()
