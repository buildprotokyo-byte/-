"""37周目の測定(トライアルB): 建具表の見出しは、そもそも1つも当たっていないのか。

基準は `docs/b_door_headings_criteria.md`(測る前にコミット済み)。

36周目の反省から、**建具番号を数から外す。** 建具表でない表で建具表の役割が
当たらないのは当たり前なので、その当たり前を数えないための測り方。

**出すのは件数と割合だけ。見出しの文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_headings --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import TableRegion, find_tables
from axes.image_axis.schedule_tables import (
    DOOR_COLUMN_SYNONYMS,
    FINISH_COLUMN_SYNONYMS,
    HEADER_SEARCH_ROWS,
    _role_of,
)

DOOR_SUPPORTING = tuple(r for r in DOOR_COLUMN_SYNONYMS if r != "建具番号")
FINISH_SUPPORTING = tuple(r for r in FINISH_COLUMN_SYNONYMS if r != "室名")


def roles_of(table: TableRegion, synonyms) -> set[str]:
    """見出しの探索範囲で当たった役割の名前。**列の文字は出さない。**"""
    found: set[str] = set()
    for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
        for cell in table.rows[row_index]:
            role = _role_of(cell.text, synonyms)
            if role is not None:
                found.add(role)
    return found


def count(tables: list[TableRegion], synonyms, required: str, supporting: tuple[str, ...]):
    per_table = []
    for table in tables:
        roles = roles_of(table, synonyms)
        per_table.append((required in roles, roles & set(supporting)))

    f1 = sum(1 for _, s in per_table if s)
    f2 = collections.Counter(len(s) for _, s in per_table if s)
    f3 = collections.Counter()
    for _, s in per_table:
        for role in s:
            f3[role] += 1
    f4 = sum(1 for has_req, s in per_table if len(s) >= 2 and not has_req)
    f5 = sum(1 for has_req, s in per_table if has_req and not s)
    return per_table, f1, f2, f3, f4, f5


def report(name: str, tables, synonyms, required: str, supporting):
    print(f"\n=== {name} ===")
    _, f1, f2, f3, f4, f5 = count(tables, synonyms, required, supporting)
    print(f"F1 付き添いの役割が1つでも当たった表: {f1} / {len(tables)}")
    print(f"F2 当たった数の分布: {dict(sorted(f2.items()))}")
    print("F3 役割ごとの内訳:")
    for role in supporting:
        print(f"     {role}: {f3.get(role, 0)} 個の表")
    print(f"F4 付き添い2個以上あるが{required}の列が無い表: {f4}")
    print(f"F5 {required}はあるが付き添いが0の表: {f5}")
    return f1, f4, f5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--synthetic", type=Path)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    tables = [t for i in range(page_count) for t in find_tables(args.pdf, i)]
    print(f"図面: {page_count} ページ / 罫線の表 {len(tables)} 個")

    f1, f4, f5 = report("建具表の付き添い", tables, DOOR_COLUMN_SYNONYMS,
                        "建具番号", DOOR_SUPPORTING)
    # F6 / 対照2
    report("F6・対照2 内装仕上表の付き添い", tables, FINISH_COLUMN_SYNONYMS,
           "室名", FINISH_SUPPORTING)

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        _, s1, s2, _, _, _ = count(syn, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_SUPPORTING)
        print(f"対照1 合成の建具表: 表 {len(syn)} 個 / 付き添いが当たった表 {s1}"
              f" / 分布 {dict(sorted(s2.items()))}")
    else:
        print("対照1 合成の建具表: --synthetic が渡されていないので測っていない")

    repeats = []
    for _ in range(3):
        t = [x for i in range(page_count) for x in find_tables(args.pdf, i)]
        _, r1, _, _, _, _ = count(t, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_SUPPORTING)
        repeats.append(r1)
    print(f"対照3 反復 3 回(F1): {repeats}")

    print("\n=== 分かれ道(基準に先に書いたもの) ===")
    if f1 == 0:
        print("   F1 = 0 → 見出しの言い方が図面とまったく合っていない")
    elif f4 >= 1:
        print(f"   F4 = {f4} ≧ 1 → 列の切り出しか表のまたぎが落ちどころ")
    elif f1 >= 1 and f4 == 0 and f5 >= 1:
        print("   F1 ≧ 1 かつ F4 = 0 かつ 建具番号ありの表は付き添い 0"
              " → (あ)の側。建具表は罫線の表として存在しない")
    else:
        print("   どれにも当てはまらない → 名指ししない")


if __name__ == "__main__":
    main()
