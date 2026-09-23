"""15周目の測定: 記号名の正規化と別名表で、届く行が増えるかを測る。

**抽出と採点を分ける。** 表(語彙と別名)は
`estimating/examples/synthetic_symbol_aliases.json` に**測る前に**書いてある。
**正解ファイルの品目名からは作っていない。**

**出すのは件数と割合だけ。** 実案件の品目名・記号名・個数そのものは印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_symbol_aliases --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path

from benchmarks.measure_symbol_counts import COUNTED_PAGE, run_path, symbol_rows
from estimating.symbol_aliases import (
    AliasTable,
    load_alias_table,
    normalise_symbol_name,
    parse_alias_table,
)
from intake.symbol_counts import SymbolCount

SYNTHETIC_ALIASES = Path("estimating/examples/synthetic_symbol_aliases.json")

#: 14 周目の語彙(基準の文書 0 章)。
FOUR_WORDS: tuple[str, ...] = ("コンセント", "スイッチ", "配線", "TEL")

#: 対照1 に使う語。**26 行のどれにも出ないことを測る前に確かめてある**
#: (`docs/a1_symbol_alias_criteria.md` 8 節)。
UNRELATED_WORDS: tuple[str, ...] = (
    "保険", "運搬", "養生", "値引", "諸経費", "現場管理", "交通費",
)

#: 採点に見る欄。14 周目と同じ(比べられるようにするため)。
SCORED_FIELDS = ("canonical_work_item", "work_item", "middle_category")


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(key) or "") for key in SCORED_FIELDS)


def reach_plain(words: tuple[str, ...], rows: list[dict]) -> int:
    """**14 周目の突き合わせそのもの**(NFKC と大文字化だけ、別名なし)。"""
    needles = [unicodedata.normalize("NFKC", w).upper() for w in words]
    return sum(
        1
        for row in rows
        if any(n in unicodedata.normalize("NFKC", _row_text(row)).upper() for n in needles)
    )


def reach_normalised(words: tuple[str, ...], rows: list[dict]) -> int:
    """15 周目の正規化を両側にかけた突き合わせ。**別名は使わない。**"""
    needles = [normalise_symbol_name(w) for w in words]
    return sum(
        1
        for row in rows
        if any(n and n in normalise_symbol_name(_row_text(row)) for n in needles)
    )


def reach_table(table: AliasTable, rows: list[dict]) -> int:
    """別名表で当たった行の数。"""
    return sum(1 for row in rows if table.found_in(_row_text(row)))


def subset_table(table: AliasTable, canonicals: tuple[str, ...]) -> AliasTable:
    """表のうち、指定した代表だけを残す。"""
    return AliasTable(
        canonical_names=tuple(c for c in table.canonical_names if c in canonicals),
        lookup={k: v for k, v in table.lookup.items() if v in canonicals},
        table_id=table.table_id + "(一部)",
    )


def words_only_table(words: tuple[str, ...]) -> AliasTable:
    """語彙だけ(別名なし)の表。"""
    return parse_alias_table(
        {"format_version": 1, "symbols": [{"canonical": w} for w in words]}
    )


def random_alias_table(
    table: AliasTable, rng: random.Random
) -> AliasTable:
    """**でたらめな別名の表。** 代表はそのまま、別名だけ同じ数の乱文字に替える。"""
    letters = "あいうえおかきくけこさしすせそたちつてと"
    lookup: dict[str, str] = {}
    for canonical in table.canonical_names:
        lookup[normalise_symbol_name(canonical)] = canonical
    for key, canonical in table.lookup.items():
        if key == normalise_symbol_name(canonical):
            continue
        fake = "".join(rng.choice(letters) for _ in range(max(2, len(key))))
        lookup[normalise_symbol_name(fake)] = canonical
    return AliasTable(table.canonical_names, lookup, table.table_id + "(でたらめ)")


def rotated_table(table: AliasTable) -> AliasTable:
    """**対応先をずらした表。** 別名の寄せ先を 1 つ後ろの代表に付け替える。"""
    names = table.canonical_names
    shift = {name: names[(i + 1) % len(names)] for i, name in enumerate(names)}
    lookup = {key: shift[value] for key, value in table.lookup.items()}
    return AliasTable(names, lookup, table.table_id + "(ずらし)")


def other_rows(golden_path: Path, kind: str) -> list[dict]:
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    return [
        item
        for item in payload["expected_items"]
        if item.get("expected_source_type") == kind
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    table = load_alias_table(SYNTHETIC_ALIASES)
    eleven = table.canonical_names
    four_table = subset_table(table, FOUR_WORDS)

    print(f"ゴールデンの symbol_count 行: {len(rows)}")
    print(f"別名表: 代表 {len(eleven)} 語 / 言い方 {len(table.lookup)} 通り")

    print("\n=== 本測定 ===")
    m0 = reach_plain(FOUR_WORDS, rows)
    m1 = reach_normalised(FOUR_WORDS, rows)
    m2 = reach_table(four_table, rows)
    m3 = reach_table(table, rows)
    m_words11 = reach_table(words_only_table(eleven), rows)
    print(f"M0 14周目のまま(4語)          : {m0} / {len(rows)}")
    print(f"M1 4語 + 正規化だけ            : {m1} / {len(rows)}")
    print(f"M2 4語 + 正規化 + 4語の別名    : {m2} / {len(rows)}")
    print(f"M3 11語 + 正規化 + 別名すべて  : {m3} / {len(rows)}")
    print(f"   (参考) 11語 + 正規化、別名なし: {m_words11} / {len(rows)}")

    entries = [SymbolCount(w, 3, page_number=COUNTED_PAGE) for w in eleven]
    path = run_path(entries)
    print(f"M4 自動確定: {path['確定した数量']}(作られた数量 {path['作られた数量']} / 見積の行 {path['当たった見積の行']})")

    print("\n=== 対照 ===")
    unrelated = words_only_table(UNRELATED_WORDS)
    print(f"対照1 無関係な語 {len(UNRELATED_WORDS)} 語: 届いた行 {reach_table(unrelated, rows)}")

    rng = random.Random(args.seed)
    fakes = [reach_table(random_alias_table(table, rng), rows) for _ in range(10)]
    print(f"対照2 でたらめな別名 10 回: {fakes}(本測定 {m3})")

    rotated = reach_table(rotated_table(table), rows)
    print(
        f"対照3 対応先のずらし: {rotated}(本測定 {m3})"
        "  ※ 鍵の集合が変わらないので、この指標では**構造上かならず同じになる**"
    )

    repeats = [reach_table(load_alias_table(SYNTHETIC_ALIASES), rows) for _ in range(3)]
    print(f"対照4 反復 3 回: {repeats}")

    for kind in ("geometry_derived", "explicit_text", "standard_rule"):
        others = other_rows(args.golden, kind)
        hit = reach_table(table, others)
        print(
            f"対照5 {kind} の行に当てる: {hit} / {len(others)} "
            f"({hit / len(others):.3f}) vs 本測定 {m3}/{len(rows)} ({m3 / len(rows):.3f})"
        )


if __name__ == "__main__":
    main()
