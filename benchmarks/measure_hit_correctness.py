"""17周目の測定: 「届いた」が「正しい記号に届いた」かを測る。

正解ファイルの `trigger_terms`(行ごとの手がかり語)は、**採点側でだけ使う。**
`usage_policy` に「`expected_items` は生成のあとの採点にだけ使う」と書いてあるとおり、
**抽出の側(語彙)には渡さない。** 語彙は 16 周目のものをそのまま使う。

**出すのは件数と割合だけ。** 手がかり語そのもの・品目名・個数は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_hit_correctness --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from benchmarks.measure_knowledge_vocabulary import KNOWLEDGE_VOCABULARY
from benchmarks.measure_symbol_aliases import (
    SYNTHETIC_ALIASES,
    UNRELATED_WORDS,
    _row_text,
    subset_table,
    words_only_table,
)
from benchmarks.measure_symbol_counts import symbol_rows
from estimating.symbol_aliases import AliasTable, load_alias_table

#: 判定の区分。
HIT_CORRECT = "正しく当たった"
HIT_WRONG = "取り違えた"
HIT_UNKNOWN = "判定できない"


def hit_names(table: AliasTable, row: dict) -> tuple[str, ...]:
    """**正解の手がかり語を見ない。** 品目名に当たった代表の集合。"""
    return table.found_in(_row_text(row))


def true_names(table: AliasTable, row: dict) -> tuple[str, ...]:
    """**採点側だけ。** その行の手がかり語を語彙に通して得た代表の集合。"""
    out: list[str] = []
    for term in row.get("trigger_terms") or []:
        for name in table.found_in(str(term)):
            if name not in out:
                out.append(name)
    return tuple(out)


def judge(hit: tuple[str, ...], true: tuple[str, ...]) -> str | None:
    if not hit:
        return None
    if not true:
        return HIT_UNKNOWN
    return HIT_CORRECT if set(hit) & set(true) else HIT_WRONG


def score(table: AliasTable, rows: list[dict], *, shift: int = 0) -> dict[str, int]:
    """`shift` は対照1(行の入れ替え)。**突き合わせる相手だけをずらす。**"""
    counts: collections.Counter[str] = collections.Counter()
    ambiguous = 0
    reached = 0
    for index, row in enumerate(rows):
        hit = hit_names(table, row)
        if not hit:
            continue
        reached += 1
        if len(hit) > 1:
            ambiguous += 1
        partner = rows[(index + shift) % len(rows)]
        verdict = judge(hit, true_names(table, partner))
        if verdict is not None:
            counts[verdict] += 1
    return {
        "届いた": reached,
        HIT_CORRECT: counts[HIT_CORRECT],
        HIT_WRONG: counts[HIT_WRONG],
        HIT_UNKNOWN: counts[HIT_UNKNOWN],
        "曖昧": ambiguous,
    }


def show(label: str, result: dict[str, int]) -> None:
    reached = result["届いた"] or 1
    print(
        f"{label}: 届いた {result['届いた']} / "
        f"正しく {result[HIT_CORRECT]}({result[HIT_CORRECT] / reached:.3f}) / "
        f"取り違え {result[HIT_WRONG]} / 判定できない {result[HIT_UNKNOWN]} / "
        f"曖昧 {result['曖昧']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    knowledge = load_alias_table(KNOWLEDGE_VOCABULARY)
    guessed = load_alias_table(SYNTHETIC_ALIASES)

    print(f"ゴールデンの symbol_count 行: {len(rows)}")
    print(f"手がかり語を持つ行: {sum(1 for r in rows if r.get('trigger_terms'))} / {len(rows)}")

    print("\n=== 本測定 ===")
    main_result = score(knowledge, rows)
    show("知識の語彙(61語)  ", main_result)
    show("当て推量の語彙(11語)", score(guessed, rows))

    print("\nP5 語ごとに当たった行の数(2 行以上に当たった語だけ):")
    spread = []
    for name in knowledge.canonical_names:
        one = subset_table(knowledge, (name,))
        hit = sum(1 for row in rows if hit_names(one, row))
        if hit >= 2:
            spread.append(hit)
    print(f"   2 行以上に当たった語: {len(spread)} 語 / 当たった行数の並び: {sorted(spread, reverse=True)}")

    print("\n=== 対照 ===")
    for shift in (1, 5, 13):
        show(f"対照1 行を {shift:2d} ずらす   ", score(knowledge, rows, shift=shift))
    unrelated = words_only_table(UNRELATED_WORDS)
    show("対照2 無関係な語 7 語 ", score(unrelated, rows))
    repeats = [score(load_alias_table(KNOWLEDGE_VOCABULARY), rows)[HIT_CORRECT] for _ in range(3)]
    print(f"対照3 反復 3 回: 正しく当たった行 {repeats}")


if __name__ == "__main__":
    main()
