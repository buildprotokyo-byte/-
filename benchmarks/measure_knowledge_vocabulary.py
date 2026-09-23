"""16周目の測定: 15周目の 21/26 が「正解に合わせた結果」かどうかを測る。

語彙の出どころを、**正解を一度も見ていない側**に移して測り直す
(`estimating/examples/knowledge_symbol_vocabulary.json`)。

**出すのは件数と割合だけ。** 実案件の品目名・記号名・個数そのものは印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_knowledge_vocabulary --golden <採点用.json>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.measure_symbol_aliases import (
    SYNTHETIC_ALIASES,
    UNRELATED_WORDS,
    _row_text,
    other_rows,
    reach_table,
    words_only_table,
)
from benchmarks.measure_symbol_counts import COUNTED_PAGE, run_path, symbol_rows
from estimating.symbol_aliases import load_alias_table
from intake.symbol_counts import SymbolCount

KNOWLEDGE_VOCABULARY = Path("estimating/examples/knowledge_symbol_vocabulary.json")


def hit_rows(table, rows) -> set[int]:
    return {i for i, row in enumerate(rows) if table.found_in(_row_text(row))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    guessed = load_alias_table(SYNTHETIC_ALIASES)
    knowledge = load_alias_table(KNOWLEDGE_VOCABULARY)

    print(f"ゴールデンの symbol_count 行: {len(rows)}")
    print(f"15周目の語彙(当て推量): {len(guessed.canonical_names)} 語")
    print(f"16周目の語彙(知識から) : {len(knowledge.canonical_names)} 語")

    g = hit_rows(guessed, rows)
    k = hit_rows(knowledge, rows)

    print("\n=== 本測定 ===")
    print(f"N0 15周目の語彙で届いた   : {len(g)} / {len(rows)}")
    print(f"N1 知識の語彙で届いた     : {len(k)} / {len(rows)}")
    print(f"N2 両方が届いた           : {len(g & k)} / {len(rows)}")
    print(f"N3 知識だけ {len(k - g)} 行 / 当て推量だけ {len(g - k)} 行")
    print(f"   どちらも届かない       : {len(rows) - len(g | k)} / {len(rows)}")

    entries = [
        SymbolCount(name, 3, page_number=COUNTED_PAGE)
        for name in knowledge.canonical_names
    ]
    path = run_path(entries)
    print(f"N4 自動確定: {path['確定した数量']}(作られた数量 {path['作られた数量']})")

    print("\n=== 対照 ===")
    unrelated = words_only_table(UNRELATED_WORDS)
    print(f"対照1 無関係な語 {len(UNRELATED_WORDS)} 語: 届いた行 {reach_table(unrelated, rows)}")

    base = len(k) / len(rows)
    print(f"対照2 別の種類の行に当てる(記号の行では {len(k)}/{len(rows)} = {base:.3f})")
    worst = 0.0
    for kind in ("geometry_derived", "explicit_text", "standard_rule"):
        others = other_rows(args.golden, kind)
        hit = reach_table(knowledge, others)
        ratio = hit / len(others)
        worst = max(worst, ratio)
        print(f"   {kind}: {hit} / {len(others)} = {ratio:.3f}")
    print(f"   いちばん高い割合 {worst:.3f} は、記号の行の割合の半分 {base / 2:.3f} 未満か: "
          f"{'はい' if worst < base / 2 else 'いいえ'}")

    repeats = [len(hit_rows(load_alias_table(KNOWLEDGE_VOCABULARY), rows)) for _ in range(3)]
    print(f"対照3 反復 3 回: {repeats}")


if __name__ == "__main__":
    main()
