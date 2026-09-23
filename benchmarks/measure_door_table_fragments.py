"""42周目の測定(トライアルB): 建具表は、1つの表が割れているのか。

基準は `docs/b_door_table_fragments_criteria.md`(測る前にコミット済み)。

**くっつけ方も当て方もこのファイルの中だけ。実装には手を入れない。**

**出すのは件数と割合だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_table_fragments --pdf <匿名化v2.pdf>
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

SLACKS = (0.0, 2.0, 6.0, 12.0)


@dataclass
class Merged:
    """くっつけた塊。元の表の行を上から並べただけ。**升目の文字はそのまま。**"""

    rows: list
    row_count: int


def touches(a, b, slack: float) -> bool:
    ax0, ay0, ax1, ay1 = a.rect_pt
    bx0, by0, bx1, by1 = b.rect_pt
    return not (ax1 + slack < bx0 or bx1 + slack < ax0
                or ay1 + slack < by0 or by1 + slack < ay0)


def merge(tables, slack: float) -> list[Merged]:
    """同じページで枠が接する表を、推移的に1つの塊にする。"""
    out: list[Merged] = []
    by_page = collections.defaultdict(list)
    for t in tables:
        by_page[t.page_index].append(t)
    for page_tables in by_page.values():
        n = len(page_tables)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                if touches(page_tables[i], page_tables[j], slack):
                    parent[find(i)] = find(j)
        groups = collections.defaultdict(list)
        for i in range(n):
            groups[find(i)].append(page_tables[i])
        for members in groups.values():
            members.sort(key=lambda t: (t.rect_pt[1], t.rect_pt[0]))
            rows = [r for t in members for r in t.rows]
            out.append(Merged(rows=rows, row_count=len(rows)))
    return out


def passed(groups, synonyms, required, min_supporting, roles_of) -> int:
    return sum(1 for g in groups
               if recognise(g, synonyms, required, min_supporting, roles_of) is not None)


def ambiguous(groups, synonyms, roles_of) -> int:
    n = 0
    for g in groups:
        for row in g.rows[:3]:
            for cell in row:
                if len(roles_of(cell.text, synonyms)) >= 2:
                    n += 1
    return n


def role_hits(groups, synonyms, required, roles_of) -> dict[str, int]:
    hits = collections.Counter()
    for g in groups:
        seen: set[str] = set()
        for row in g.rows[:3]:
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
    print(f"図面: {page_count} ページ / 内側の表 {len(inside)} / 外側の表 {len(outside)}")

    door = (DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS)
    best = []
    slack0_finish = None
    for slack in SLACKS:
        gi, go = merge(inside, slack), merge(outside, slack)
        print(f"\n########## SLACK = {slack:g} pt ##########")
        print(f"P1 くっつけたあとの塊: 内側 {len(gi)} / 外側 {len(go)}")
        for name, roles_of in WAYS:
            p2 = passed(gi, *door, roles_of)
            p3 = passed(go, *door, roles_of)
            p4 = (p3 / p2) if p2 else None
            p5 = ambiguous(gi + go, DOOR_COLUMN_SYNONYMS, roles_of)
            p7 = passed(merge(inside + outside, slack), FINISH_COLUMN_SYNONYMS, "室名", 1, roles_of)
            if slack == 0.0 and name == WAYS[0][0]:
                slack0_finish = {}
            if slack == 0.0:
                slack0_finish[name] = p7
            best.append((slack, name, p2, p3, p4, p5, p7))
            print(f"   {name}: P2 当たり {p2} / P3 誤爆 {p3}"
                  f" / P4 {'-' if p4 is None else f'{p4:.3f}'}"
                  f" / P5 取り違え {p5} / P7 仕上表 {p7}")
            print(f"      P6 役割ごとの当たり(内側): {role_hits(gi, DOOR_COLUMN_SYNONYMS, '建具番号', roles_of)}")

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        ok = {}
        for slack in SLACKS:
            g = merge(syn, slack)
            ok[slack] = {n.split()[0]: passed(g, *door, f) for n, f in WAYS}
        allok = all(all(v.values()) for v in ok.values())
        print(f"対照1 合成の建具表を 4 通りの SLACK でくっつける: {ok}"
              f" / どれでも通り続けたか: {'はい' if allok else 'いいえ'}")
    else:
        print("対照1: --synthetic が渡されていないので測っていない")

    s0 = {n.split()[0]: p2 for sl, n, p2, *_ in best if sl == 0.0}
    repro = s0 == {"K0": 0, "K1": 1, "K2a": 0, "K2b": 0}
    print(f"対照2 SLACK=0 が41周目を再現したか: {s0} → {'はい' if repro else 'いいえ'}")

    print("対照3 仕上表が SLACK=0 から減っていないか:")
    for sl, n, _, _, _, _, p7 in best:
        base = slack0_finish[n]
        if sl != 0.0 and p7 < base:
            print(f"     SLACK {sl:g} / {n}: {p7} < {base} ×")
    print("     (×が出ていなければ減っていない)")

    reps = [passed(merge(inside, 6.0), *door, k2b_tokens) for _ in range(3)]
    print(f"対照4 反復 3 回(SLACK=6 + K2b): {reps}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not repro:
        print("   対照2 が再現しなかった → くっつけ方が SLACK=0 でも何かを変えている。結論を出さない。")
        return
    winners = []
    for sl, n, p2, p3, p4, p5, p7 in best:
        c1 = p2 >= 2
        c2 = p4 is not None and p4 < 0.5
        c3 = p5 == 0
        c4 = p7 >= slack0_finish[n]
        if c1 and c2 and c3 and c4:
            winners.append((sl, n, p2, p4))
    reached = [b for b in best if b[2] >= 2]
    if winners:
        winners.sort(key=lambda w: (w[0], -w[2]))
        sl, n, p2, p4 = winners[0]
        print(f"   → **答え: 表が割れていた。** 先に決めた優先順位で SLACK={sl:g} / {n}"
              f"(当たり {p2}、誤爆比 {p4:.3f})。実装案を書く。PR のまま待つ。")
    elif reached:
        print(f"   → **割れてはいるが、くっつけ方が粗い。**(当たり 2 以上が {len(reached)} 通り)"
              " 接し方の向きを分ける周へ。")
    else:
        print("   → **割れているのでもない。** 6 つ試して 6 つとも違った。"
              "建具表の追跡をいったん止め、判断待ちとしておーちゃんに出す。")


if __name__ == "__main__":
    main()
