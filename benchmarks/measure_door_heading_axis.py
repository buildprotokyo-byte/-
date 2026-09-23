"""41周目の測定(キラークエスチョン): 建具表の見出しは、本当に「行」にあるのか。

基準は `docs/b_door_heading_axis_criteria.md`(測る前にコミット済み)。

**転置も当て方もこのファイルの中だけ。実装には手を入れない。**

**出すのは件数と割合だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_heading_axis --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
from dataclasses import dataclass
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.schedule_tables import (
    DOOR_COLUMN_SYNONYMS,
    DOOR_MIN_SUPPORTING_COLUMNS,
    FINISH_COLUMN_SYNONYMS,
)

from benchmarks.measure_door_heading_shape import WAYS, k2b_tokens, recognise


@dataclass
class Transposed:
    """行と列を入れ替えただけの表。**升目の文字はそのまま。**

    `recognise()` は `rows` と `row_count` しか見ないので、この2つで足りる。
    """

    rows: list[list]
    row_count: int


def transpose(table) -> Transposed:
    rows = table.rows
    if not rows:
        return Transposed(rows=[], row_count=0)
    width = max(len(r) for r in rows)
    flipped = [[r[c] for r in rows if c < len(r)] for c in range(width)]
    return Transposed(rows=flipped, row_count=len(flipped))


def passed(tables, synonyms, required, min_supporting, roles_of) -> int:
    return sum(1 for t in tables
               if recognise(t, synonyms, required, min_supporting, roles_of) is not None)


def which_pass(tables, synonyms, required, min_supporting, roles_of) -> set[int]:
    return {i for i, t in enumerate(tables)
            if recognise(t, synonyms, required, min_supporting, roles_of) is not None}


def ambiguous(tables, synonyms, roles_of) -> int:
    n = 0
    for table in tables:
        for row in table.rows[:3]:
            for cell in row:
                if len(roles_of(cell.text, synonyms)) >= 2:
                    n += 1
    return n


def role_hits(tables, synonyms, required, roles_of) -> dict[str, int]:
    hits = collections.Counter()
    for table in tables:
        seen: set[str] = set()
        for row in table.rows[:3]:
            for cell in row:
                seen |= roles_of(cell.text, synonyms)
        for role in seen:
            if role != required:
                hits[role] += 1
    return dict(sorted(hits.items()))


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
    t_inside = [transpose(t) for t in inside]
    t_outside = [transpose(t) for t in outside]
    print(f"図面: {page_count} ページ / 内側の表 {len(inside)} / 外側の表 {len(outside)}")

    door = (DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS)
    best_n1 = 0
    for name, roles_of in WAYS:
        n1 = passed(t_inside, *door, roles_of)
        n2 = passed(t_outside, *door, roles_of)
        n3 = (n2 / n1) if n1 else None
        n4 = ambiguous(t_inside + t_outside, DOOR_COLUMN_SYNONYMS, roles_of)
        both = which_pass(inside, *door, roles_of) & which_pass(t_inside, *door, roles_of)
        n7 = passed([transpose(t) for t in inside + outside],
                    FINISH_COLUMN_SYNONYMS, "室名", 1, roles_of)
        best_n1 = max(best_n1, n1)
        print(f"\n=== 転置 + {name} ===")
        print(f"   N1 当たり(転置・内側): {n1} / {len(inside)}")
        print(f"   N2 誤爆(転置・外側): {n2} / {len(outside)}")
        print(f"   N3 誤爆 ÷ 当たり: {'-' if n3 is None else f'{n3:.3f}'}")
        print(f"   N4 取り違え: {n4}")
        print(f"   N5 役割ごとの当たり(転置・内側): {role_hits(t_inside, DOOR_COLUMN_SYNONYMS, '建具番号', roles_of)}")
        print(f"   N6 転置前と転置後の両方で通った表: {len(both)}")
        print(f"   N7 内装仕上表(転置): {n7}")

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        plain = {n: passed(syn, *door, f) for n, f in WAYS}
        flipped = {n: passed([transpose(t) for t in syn], *door, f) for n, f in WAYS}
        print(f"対照1 合成の建具表を転置: {flipped}"
              f" / 通らなかったか: {'はい' if not any(flipped.values()) else 'いいえ'}")
        print(f"対照2 合成の建具表を転置せず: {plain}"
              f" / 4 通りとも通ったか: {'はい' if all(plain.values()) else 'いいえ'}")
    else:
        print("対照1・2: --synthetic が渡されていないので測っていない")

    plain_inside = {n.split()[0]: passed(inside, *door, f) for n, f in WAYS}
    repro = plain_inside == {"K0": 0, "K1": 1, "K2a": 0, "K2b": 0}
    print(f"対照3 転置なしの内側が40周目を再現したか: {plain_inside}"
          f" → {'はい' if repro else 'いいえ'}")

    reps = [passed([transpose(t) for t in inside], *door, k2b_tokens) for _ in range(3)]
    print(f"対照4 反復 3 回(転置 + K2b): {reps}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not repro:
        print("   対照3 が再現しなかった → 測り方が変わっている。結論を出さない。")
        return
    winners = []
    for name, roles_of in WAYS:
        n1 = passed(t_inside, *door, roles_of)
        n2 = passed(t_outside, *door, roles_of)
        n3 = (n2 / n1) if n1 else None
        n4 = ambiguous(t_inside + t_outside, DOOR_COLUMN_SYNONYMS, roles_of)
        c1, c2, c3 = n1 >= 2, (n3 is not None and n3 < 0.5), n4 == 0
        print(f"   転置 + {name}: 当たり {n1} {'◯' if c1 else '×'} /"
              f" 誤爆 {'-' if n3 is None else f'{n3:.3f}'} {'◯' if c2 else '×'} /"
              f" 取り違え {n4} {'◯' if c3 else '×'}")
        if c1 and c2 and c3:
            winners.append(name)
    if winners:
        print(f"   → **答え: 見出しは列にあった。** 直す場所は表の向き({winners})。PR のまま待つ。")
    elif best_n1 >= 2:
        print("   → **向きは効いているが、それだけでは足りない。** 向きと当て方を組み合わせる周へ。")
    else:
        print("   → **答え: 見出しは行にも列にも無い。** 語彙でも向きでもないので、"
              "表の升目の取り方そのもの(結合された升目・続き表・分割された表)を疑う周へ。")


if __name__ == "__main__":
    main()
