"""40周目の測定(トライアルB): 当て方の「形」を変えたら誤爆せずに届くか。

基準は `docs/b_door_heading_shape_criteria.md`(測る前にコミット済み)。

**当て方はこのファイルの中だけに書く。実装には手を入れない。**

**出すのは件数と割合だけ。図面の文字は1文字も出さない。**

実行::

    .venv/bin/python -m benchmarks.measure_door_heading_shape --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
import re
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

#: 区切り。空白と、見出しでよく使われる記号。
_SPLIT = re.compile(r"[\s・･/／\\,、.。:：;；×x✕－ー―‐\-−~〜|｜()（）\[\]［］{}｛｝]+")


def k0_exact(text: str, synonyms) -> set[str]:
    """K0 いまのまま(完全一致)。"""
    role = _role_of(text, synonyms)
    return {role} if role is not None else set()


def k1_contains(text: str, synonyms) -> set[str]:
    """K1 案1 含み一致(39周目の再現)。"""
    key = _match_role(text)
    if not key:
        return set()
    return {r for r, names in synonyms.items() if any(n and n in key for n in names)}


def k2a_suffix(text: str, synonyms) -> set[str]:
    """K2a 後方一致。升目の文字が言い換えで**終わる**ときだけ当てる。"""
    key = _match_role(text)
    if not key:
        return set()
    return {r for r, names in synonyms.items() if any(n and key.endswith(n) for n in names)}


def k2b_tokens(text: str, synonyms) -> set[str]:
    """K2b 区切ってから完全一致。割った片のどれかが言い換えとちょうど同じなら当てる。"""
    key = _match_role(text)
    if not key:
        return set()
    tokens = {t for t in _SPLIT.split(key) if t}
    return {r for r, names in synonyms.items() if tokens & set(names)}


WAYS = (("K0 いまのまま(完全一致)", k0_exact),
        ("K1 案1 含み一致", k1_contains),
        ("K2a 後方一致", k2a_suffix),
        ("K2b 区切って完全一致", k2b_tokens))


def recognise(table: TableRegion, synonyms, required: str, min_supporting: int, roles_of):
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


def passed(tables, synonyms, required, min_supporting, roles_of) -> int:
    return sum(1 for t in tables
               if recognise(t, synonyms, required, min_supporting, roles_of) is not None)


def ambiguous(tables, synonyms, roles_of) -> int:
    n = 0
    for table in tables:
        for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
            for cell in table.rows[row_index]:
                if len(roles_of(cell.text, synonyms)) >= 2:
                    n += 1
    return n


def role_hits(tables, synonyms, required, roles_of) -> dict[str, int]:
    hits = collections.Counter()
    for table in tables:
        seen: set[str] = set()
        for row_index in range(min(HEADER_SEARCH_ROWS, table.row_count)):
            for cell in table.rows[row_index]:
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
    print("**内側・外側は当たり・誤爆の代わりの目印であって正解ではない。**")

    results = {}
    for name, roles_of in WAYS:
        m1 = passed(inside, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS, roles_of)
        m2 = passed(outside, DOOR_COLUMN_SYNONYMS, "建具番号", DOOR_MIN_SUPPORTING_COLUMNS, roles_of)
        m3 = (m2 / m1) if m1 else None
        m4 = ambiguous(inside + outside, DOOR_COLUMN_SYNONYMS, roles_of)
        m6 = passed(inside + outside, FINISH_COLUMN_SYNONYMS, "室名", 1, roles_of)
        results[name] = (m1, m2, m3, m4, m6)
        print(f"\n=== {name} ===")
        print(f"   M1 当たり(内側で通った表): {m1} / {len(inside)}")
        print(f"   M2 誤爆(外側で通った表): {m2} / {len(outside)}")
        print(f"   M3 誤爆 ÷ 当たり: {'-' if m3 is None else f'{m3:.3f}'}")
        print(f"   M4 取り違え(升目が2役割以上): {m4}")
        print(f"   M5 役割ごとの当たり(内側): {role_hits(inside, DOOR_COLUMN_SYNONYMS, '建具番号', roles_of)}")
        print(f"   M6 内装仕上表で通った表: {m6}")

    print("\n=== 対照 ===")
    if args.synthetic:
        syn = list(find_tables(args.synthetic, 0))
        oks = {n: passed(syn, DOOR_COLUMN_SYNONYMS, "建具番号",
                         DOOR_MIN_SUPPORTING_COLUMNS, f) for n, f in WAYS}
        print(f"対照1 合成の建具表: {oks}"
              f" / 4 通りとも通ったか: {'はい' if all(oks.values()) else 'いいえ'}")
    else:
        print("対照1: --synthetic が渡されていないので測っていない")

    k1 = results["K1 案1 含み一致"]
    repro = (k1[0] == 1 and k1[1] == 1 and k1[3] == 7)
    print(f"対照2 K1 が39周目を再現したか(内側1/外側1/取り違え7): "
          f"{k1[0]}/{k1[1]}/{k1[3]} → {'はい' if repro else 'いいえ'}")

    k0_m6 = results["K0 いまのまま(完全一致)"][4]
    print(f"対照3 内装仕上表が K0({k0_m6})から減っていないか: "
          + " / ".join(f"{n.split()[0]} {v[4]}{'◯' if v[4] >= k0_m6 else '×'}"
                       for n, v in results.items()))

    reps = []
    for _ in range(3):
        t = [x for i in range(page_count) for x in find_tables(args.pdf, i) if i in inside_pages]
        reps.append(passed(t, DOOR_COLUMN_SYNONYMS, "建具番号",
                           DOOR_MIN_SUPPORTING_COLUMNS, k2b_tokens))
    print(f"対照4 反復 3 回(K2b 内側): {reps}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not repro:
        print("   対照2 が再現しなかった → 測り方が変わっている。結論を出さない。")
        return
    k0_m1 = results["K0 いまのまま(完全一致)"][0]
    ok = {}
    for name in ("K2a 後方一致", "K2b 区切って完全一致"):
        m1, m2, m3, m4, m6 = results[name]
        c1, c2, c3 = m1 - k0_m1 >= 1, (m3 is not None and m3 < 0.5), m4 == 0
        c4 = m6 >= k0_m6
        ok[name] = c1 and c2 and c3 and c4
        print(f"   {name}: 当たり {k0_m1}→{m1} {'◯' if c1 else '×'} /"
              f" 誤爆 {'-' if m3 is None else f'{m3:.3f}'} {'◯' if c2 else '×'} /"
              f" 取り違え {m4} {'◯' if c3 else '×'} /"
              f" 仕上表 {m6} {'◯' if c4 else '×'}")
    winners = [n for n, v in ok.items() if v]
    if not winners:
        print("   → **両方とも不採用。** 見出しの当て方では届かない。"
              "次は『表を先に見分けてから列を読む』向きに変える。")
    elif len(winners) == 1:
        print(f"   → **{winners[0]} を実装する周に進む。PR のまま待つ。**")
    else:
        a, b = (results[w] for w in winners)
        pick = winners[0] if a[2] < b[2] else winners[1] if b[2] < a[2] else (
            winners[0] if a[0] > b[0] else winners[1] if b[0] > a[0] else "K2b 区切って完全一致")
        print(f"   → 両方とも満たした。先に決めた優先順位で **{pick}**。")


if __name__ == "__main__":
    main()
