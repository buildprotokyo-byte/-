"""43周目の測定(トライアルB): 上限。この升目から建具表は取り出せるのか。

基準は `docs/b_door_upper_bound_criteria.md`(測る前にコミット済み)。

**見出しの探索範囲を「上から3行」から「全部の行」に広げる。**
これは実装として正しい読み方ではない(下のほうの行を見出しと言い張ることになる)。
**「この升目の集まりから建具表が取り出せるか」の上限としてだけ正しい。**
上限で届かなければ、どんな正しい読み方でも届かない。

**出すのは件数と割合だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_upper_bound --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.schedule_tables import (
    DOOR_COLUMN_SYNONYMS,
    DOOR_MIN_SUPPORTING_COLUMNS,
    FINISH_COLUMN_SYNONYMS,
)

from benchmarks.measure_door_heading_axis import Transposed, transpose
from benchmarks.measure_door_heading_shape import WAYS
from benchmarks.measure_door_table_fragments import merge

WINDOWS = (("窓3行", 3), ("窓なし", None))
AXES = (("そのまま", False), ("転置", True))
MERGES = (("くっつけない", None), ("SLACK6", 6.0))


def recognise(table, synonyms, required, min_supporting, roles_of, window):
    """`window` が None なら全部の行から見出しを探す。"""
    limit = table.row_count if window is None else min(window, table.row_count)
    best = None
    for row_index in range(limit):
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


def near_miss(table, synonyms, required, roles_of, window) -> bool:
    """建具番号と付き添いが2つ以上そろっているか(しきいを外した数、Q6)。"""
    limit = table.row_count if window is None else min(window, table.row_count)
    for row_index in range(limit):
        columns = set()
        for cell in table.rows[row_index]:
            columns |= roles_of(cell.text, synonyms)
        if required in columns and len(columns - {required}) >= 2:
            return True
    return False


def prepare(tables, flip: bool, slack):
    out = tables if slack is None else merge(tables, slack)
    if flip:
        out = [transpose(t) for t in out]
    return out


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
    rows = []
    for wname, window in WINDOWS:
        for aname, flip in AXES:
            for mname, slack in MERGES:
                gi = prepare(inside, flip, slack)
                go = prepare(outside, flip, slack)
                gf = prepare(inside + outside, flip, slack)
                for kname, roles_of in WAYS:
                    q1 = sum(1 for t in gi if recognise(t, *door, roles_of, window))
                    q2 = sum(1 for t in go if recognise(t, *door, roles_of, window))
                    q3 = (q2 / q1) if q1 else None
                    q4 = 0
                    for t in gi + go:
                        limit = t.row_count if window is None else min(window, t.row_count)
                        for row in t.rows[:limit]:
                            for cell in row:
                                if len(roles_of(cell.text, DOOR_COLUMN_SYNONYMS)) >= 2:
                                    q4 += 1
                    q5 = collections.Counter()
                    for t in gi:
                        limit = t.row_count if window is None else min(window, t.row_count)
                        seen = set()
                        for row in t.rows[:limit]:
                            for cell in row:
                                seen |= roles_of(cell.text, DOOR_COLUMN_SYNONYMS)
                        for r in seen - {"建具番号"}:
                            q5[r] += 1
                    q6 = sum(1 for t in gi
                             if near_miss(t, DOOR_COLUMN_SYNONYMS, "建具番号", roles_of, window))
                    q7 = sum(1 for t in gf
                             if recognise(t, FINISH_COLUMN_SYNONYMS, "室名", 1, roles_of, window))
                    rows.append((wname, aname, mname, kname.split()[0],
                                 q1, q2, q3, q4, dict(sorted(q5.items())), q6, q7))

    print("\n=== 全 32 通り ===")
    print(f"{'窓':<7}{'向き':<7}{'くっつけ':<9}{'当て方':<5}"
          f"{'Q1当':>4}{'Q2誤':>4}{'Q3比':>8}{'Q4違':>5}{'Q6惜':>5}{'Q7仕':>5}")
    for w, a, m, k, q1, q2, q3, q4, q5, q6, q7 in rows:
        ratio = "-" if q3 is None else f"{q3:.3f}"
        print(f"{w:<7}{a:<7}{m:<9}{k:<5}{q1:>4}{q2:>4}{ratio:>8}{q4:>5}{q6:>5}{q7:>5}")

    print("\n=== 役割ごとの当たり(窓なし・そのまま・くっつけない) ===")
    for w, a, m, k, *_rest in rows:
        if w == "窓なし" and a == "そのまま" and m == "くっつけない":
            print(f"   {k}: {_rest[4]}")

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        bad = []
        for wname, window in WINDOWS:
            for kname, roles_of in WAYS:
                n = sum(1 for t in syn if recognise(t, *door, roles_of, window))
                if not n:
                    bad.append((wname, kname.split()[0]))
        print(f"対照1 合成の建具表(そのまま・くっつけない): 通らなかった組み合わせ {bad}"
              f" / 窓を外しても通り続けたか: {'はい' if not bad else 'いいえ'}")
    else:
        print("対照1: --synthetic が渡されていないので測っていない")

    base = {k: q1 for w, a, m, k, q1, *_ in rows
            if w == "窓3行" and a == "そのまま" and m == "くっつけない"}
    repro = base == {"K0": 0, "K1": 1, "K2a": 0, "K2b": 0}
    print(f"対照2 窓3行・そのまま・くっつけない が42周目を再現したか: {base}"
          f" → {'はい' if repro else 'いいえ'}")

    fin3 = {(a, m, k): q7 for w, a, m, k, *r in rows if w == "窓3行" for q7 in [r[-1]]}
    finN = {(a, m, k): q7 for w, a, m, k, *r in rows if w == "窓なし" for q7 in [r[-1]]}
    dropped = {key: (fin3[key], finN[key]) for key in fin3 if finN[key] < fin3[key]}
    print(f"対照3 仕上表が窓を外して減った組み合わせ: {dropped if dropped else 'なし'}")

    reps = []
    for _ in range(3):
        gi = prepare(inside, False, None)
        reps.append(sum(1 for t in gi if recognise(t, *door, WAYS[1][1], None)))
    print(f"対照4 反復 3 回(窓なし・K1・内側): {reps}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not repro:
        print("   対照2 が再現しなかった → 結論を出さない。")
        return
    no_window = [r for r in rows if r[0] == "窓なし"]
    winners = [r for r in no_window
               if r[4] >= 2 and r[6] is not None and r[6] < 0.5 and r[7] == 0
               and r[10] >= fin3[(r[1], r[2], r[3])]]
    reached = [r for r in no_window if r[4] >= 2]
    best_q1 = max((r[4] for r in no_window), default=0)
    print(f"   窓を外したときの当たりの最大: {best_q1}")
    if winners:
        w = winners[0]
        print(f"   → **穴は窓だった。** {w[0]}/{w[1]}/{w[2]}/{w[3]} で当たり {w[4]}、"
              f"誤爆比 {w[6]:.3f}、取り違え {w[7]}。実装案を書く。PR のまま待つ。")
    elif reached:
        print(f"   → **窓は効いているが、それだけでは足りない。**"
              f"(当たり 2 以上が {len(reached)} 通り)**割れていることの証拠にはなる。**")
    else:
        print("   → **上限で届かない。** この図面の升目からは、この語彙で建具表は取り出せない。"
              "7 つ試して 7 つとも違った。42 周目に出した判断待ちの根拠が固くなる。")


if __name__ == "__main__":
    main()
