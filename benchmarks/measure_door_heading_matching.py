"""39周目の測定(トライアルB): 見出しの当て方を緩めたら、どれだけ当たり、どれだけ誤爆するか。

基準は `docs/b_door_heading_matching_criteria.md`(測る前にコミット済み)。

**緩め方はこのファイルの中だけに書く。実装には手を入れない。**

**出すのは件数と割合だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_heading_matching --pdf <匿名化v2.pdf>
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
    _match_role,
    _role_of,
)


def roles_exact(text: str, synonyms) -> set[str]:
    """いまの当て方(完全一致)。"""
    role = _role_of(text, synonyms)
    return {role} if role is not None else set()


def roles_loose(text: str, synonyms) -> set[str]:
    """**案1 含み一致。** 升目の文字に言い換えが含まれていれば当てる。

    ここだけの実装。実装のほうは触らない。
    """
    key = _match_role(text)
    if not key:
        return set()
    return {role for role, names in synonyms.items() if any(n and n in key for n in names)}


def recognise(table: TableRegion, synonyms, required: str, min_supporting: int, roles_of):
    """その表が建具表(または仕上表)として通るか。通るなら見出し行と役割を返す。"""
    best = None
    for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
        columns: dict[str, int] = {}
        for col_index, cell in enumerate(table.rows[row_index]):
            for role in roles_of(cell.text, synonyms):
                if role not in columns:
                    columns[role] = col_index
        if required not in columns:
            continue
        if best is None or len(columns) > len(best):
            best = columns
    if best is None:
        return None
    if len([r for r in best if r != required]) < min_supporting:
        return None
    return best


def ambiguous_cells(tables, synonyms, roles_of) -> int:
    """1 つの升目が 2 つ以上の役割に当たった回数。"""
    n = 0
    for table in tables:
        for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
            for cell in table.rows[row_index]:
                if len(roles_of(cell.text, synonyms)) >= 2:
                    n += 1
    return n


def role_hits(tables, synonyms, required: str, roles_of) -> dict[str, int]:
    hits = collections.Counter()
    for table in tables:
        seen: set[str] = set()
        for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
            for cell in table.rows[row_index]:
                seen |= roles_of(cell.text, synonyms)
        for role in seen:
            if role != required:
                hits[role] += 1
    return dict(hits)


def passed(tables, synonyms, required, min_supporting, roles_of) -> int:
    return sum(1 for t in tables
               if recognise(t, synonyms, required, min_supporting, roles_of) is not None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--synthetic", type=Path)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
        inside_pages = {i for i in range(page_count) if "建具" in doc[i].get_text()}

    inside, outside = [], []
    for i in range(page_count):
        for table in find_tables(args.pdf, i):
            (inside if i in inside_pages else outside).append(table)
    print(f"図面: {page_count} ページ / 内側(建具の語があるページ)の表 {len(inside)}"
          f" / 外側の表 {len(outside)}")
    print("**内側・外側は当たり・誤爆の代わりの目印であって正解ではない。**"
          " 内側にも凡例が混ざり、外側に続きの建具表があるかもしれない。")

    rows = []
    for name, roles_of in (("H0 いまのまま(完全一致)", roles_exact),
                           ("H1 案1(含み一致)", roles_loose)):
        a = passed(inside, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS, roles_of)
        b = passed(outside, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS, roles_of)
        rows.append((name, a, b))
        print(f"\n=== {name} ===")
        print(f"   内側で通った表: {a} / {len(inside)}")
        print(f"   外側で通った表: {b} / {len(outside)}")
        print(f"   H3 役割ごとの当たり(内側): {role_hits(inside, DOOR_COLUMN_SYNONYMS, '建具番号', roles_of)}")

    h0_in, h0_out = rows[0][1], rows[0][2]
    h1_in, h1_out = rows[1][1], rows[1][2]
    h2 = (h1_out / h1_in) if h1_in else None
    h4 = ambiguous_cells(inside + outside, DOOR_COLUMN_SYNONYMS, roles_loose)
    print(f"\nH2 誤爆 ÷ 当たり: {h1_out} / {h1_in} = "
          f"{'-' if h2 is None else f'{h2:.3f}'}")
    print(f"H4 1 つの升目が 2 つ以上の役割に当たった回数: {h4}")

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        ok = passed(syn, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS, roles_loose)
        print(f"対照1 合成の建具表に案1: 表 {len(syn)} / 通った {ok}"
              f" / 通ったか: {'はい' if ok else 'いいえ'}")
    else:
        print("対照1: --synthetic が渡されていないので測っていない")

    all_tables = inside + outside
    fin_exact = passed(all_tables, FINISH_COLUMN_SYNONYMS, "室名", 1, roles_exact)
    fin_loose = passed(all_tables, FINISH_COLUMN_SYNONYMS, "室名", 1, roles_loose)
    print(f"対照2 内装仕上表: いまのまま {fin_exact} → 案1 {fin_loose}"
          f" / 通り続けたか: {'はい' if fin_loose >= fin_exact else 'いいえ'}")

    repeats = []
    for _ in range(3):
        t = [x for i in range(page_count) for x in find_tables(args.pdf, i)
             if i in inside_pages]
        repeats.append(passed(t, DOOR_COLUMN_SYNONYMS, "建具番号",
                              DOOR_MIN_SUPPORTING_COLUMNS, roles_loose))
    print(f"対照3 反復 3 回(H1 内側): {repeats}")

    print("\n=== 採否(基準に先に書いた線) ===")
    gain = h1_in - h0_in
    c1 = gain >= 1
    c2 = h2 is not None and h2 < 0.5
    c3 = h4 == 0
    print(f"   当たり: 内側 {h0_in} → {h1_in}(増分 {gain}) / 1 個以上か: {'◯' if c1 else '×'}")
    print(f"   誤爆: H2 {'-' if h2 is None else f'{h2:.3f}'} / 0.5 未満か: {'◯' if c2 else '×'}")
    print(f"   取り違え: H4 {h4} / 0 か: {'◯' if c3 else '×'}")
    if not c1:
        print("   → **案1 は不採用。** 含み一致では届かない。案2 へ。")
    elif c1 and c2 and c3:
        print("   → **案1 を実装する周に進む。ただし判定に触るので PR のまま待つ。**")
    else:
        print("   → **保留。** 役割ごとに緩め方を変える案を次に測る。")


if __name__ == "__main__":
    main()
