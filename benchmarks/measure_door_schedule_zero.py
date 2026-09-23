"""36周目の測定(トライアルB): 建具表が 0 件になるのは、どこで落ちているからか。

基準は `docs/b_door_schedule_zero_criteria.md`(測る前にコミット済み)。

**出すのは件数とページ番号と割合だけ。**
室名・寸法・数量・建具番号・事務所名・**見出しの文字**も 1 文字も印字しない。
匿名化 v2 には塗りつぶされて見えない文字データが残っている。

実行::

    .venv/bin/python -m benchmarks.measure_door_schedule_zero --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import TableRegion, find_tables
from axes.image_axis.schedule_tables import (
    DOOR_COLUMN_SYNONYMS,
    DOOR_MIN_SUPPORTING_COLUMNS,
    FINISH_COLUMN_SYNONYMS,
    HEADER_SEARCH_ROWS,
    _find_header,
    _role_of,
)


def has_any_text(table: TableRegion) -> bool:
    """升目に文字が 1 つでもあるか。**中身は見ない。**"""
    return any(cell.text.strip() for row in table.rows for cell in row)


def roles_in_header_rows(table: TableRegion, synonyms) -> set[str]:
    """見出しの探索範囲にある行で当たった役割の名前。**列の文字は出さない。**"""
    found: set[str] = set()
    for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
        for cell in table.rows[row_index]:
            role = _role_of(cell.text, synonyms)
            if role is not None:
                found.add(role)
    return found


def diagnose(tables: list[TableRegion], synonyms, required: str, min_supporting: int):
    """L1〜L4 のどこで落ちたかを表ごとに数える。"""
    counts = collections.Counter()
    supporting_hist = collections.Counter()
    for table in tables:
        if not has_any_text(table):
            counts["L2 升目に文字が無い"] += 1
            continue
        header = _find_header(table, synonyms, required=required)
        if header is None:
            roles = roles_in_header_rows(table, synonyms)
            if not roles:
                counts["L3a 役割が1つも当たらない"] += 1
            else:
                counts[f"L3b 役割は当たるが{required}が無い"] += 1
            continue
        _, columns = header
        supporting = len([r for r in columns if r != required])
        supporting_hist[supporting] += 1
        if supporting < min_supporting:
            counts["L4 付き添いの列が足りない"] += 1
        else:
            counts["通った"] += 1
    return counts, supporting_hist


def report(name: str, tables: list[TableRegion], synonyms, required: str, min_supporting: int):
    print(f"\n=== {name} ===")
    print(f"E1 表の数: {len(tables)}")
    print(f"E2 升目に文字がある表: {sum(1 for t in tables if has_any_text(t))}")
    counts, hist = diagnose(tables, synonyms, required, min_supporting)
    with_required = sum(hist.values())
    print(f"E3 {required}らしい列が当たった表: {with_required}")
    print(f"E4 付き添いの列の数の分布: {dict(sorted(hist.items()))}"
          f" / しきい {min_supporting}")
    print("E5 落ちどころ:")
    for label, n in counts.most_common():
        print(f"     {label}: {n}")
    return counts, hist


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--synthetic", type=Path, help="対照2で使う合成の建具表 PDF")
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    tables = [t for i in range(page_count) for t in find_tables(args.pdf, i)]
    pages_with_tables = sorted({t.page_index + 1 for t in tables})
    print(f"図面: {page_count} ページ / 表のあるページ {len(pages_with_tables)}")

    door_counts, _ = report(
        "建具表(折れている経路)", tables, DOOR_COLUMN_SYNONYMS,
        "建具番号", DOOR_MIN_SUPPORTING_COLUMNS,
    )
    # E6 / 対照1: 動いている経路を同じ測り方で。
    report(
        "E6・対照1 内装仕上表(動いている経路)", tables, FINISH_COLUMN_SYNONYMS,
        "室名", 1,
    )

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = [t for t in find_tables(args.synthetic, 0)]
        c, _ = diagnose(syn, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS)
        ok = c.get("通った", 0)
        print(f"対照2 合成の建具表: 表 {len(syn)} 個 / 通った {ok} 個"
              f" / 通ったか: {'はい' if ok else 'いいえ'}")
    else:
        print("対照2 合成の建具表: --synthetic が渡されていないので測っていない")

    repeats = []
    for _ in range(3):
        t = [x for i in range(page_count) for x in find_tables(args.pdf, i)]
        c, _ = diagnose(t, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS)
        repeats.append(len(t))
    print(f"対照3 反復 3 回(表の数): {repeats}")

    print("\n=== まとめ ===")
    top = door_counts.most_common(1)
    if not top:
        print("   表が 1 つも無いので L1。")
    else:
        label, n = top[0]
        total = sum(door_counts.values())
        print(f"   いちばん多い落ちどころ: {label} {n} / {total} = {n / total:.3f}")


if __name__ == "__main__":
    main()
