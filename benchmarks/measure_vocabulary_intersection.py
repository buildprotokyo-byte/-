"""20周目の測定: 2つの語彙の交わりで誤爆が減るかを測る。

**語そのものは印字しない。** 出すのは件数と割合だけ。

実行::

    .venv/bin/python -m benchmarks.measure_vocabulary_intersection \
        --pdf <図面.pdf> --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from benchmarks.measure_drawing_vocabulary import (
    LEGEND_PAGE,
    OTHER_PAGE,
    report,
    table_of,
    words_from_pages,
)
from benchmarks.measure_hit_correctness import HIT_CORRECT, SHUFFLE_ROUNDS, score
from benchmarks.measure_knowledge_vocabulary import KNOWLEDGE_VOCABULARY
from benchmarks.measure_symbol_counts import symbol_rows
from estimating.symbol_aliases import load_alias_table, normalise_symbol_name


def intersection(drawing: tuple[str, ...], knowledge: tuple[str, ...]) -> tuple[str, ...]:
    """**両方にある語。** 正規化した形で突き合わせる。"""
    known = {normalise_symbol_name(w) for w in knowledge}
    return tuple(w for w in drawing if normalise_symbol_name(w) in known)


def loose_intersection(
    drawing: tuple[str, ...], knowledge: tuple[str, ...]
) -> tuple[str, ...]:
    """図面の語のうち、知識の語を**部分文字列として含む**もの。"""
    known = [normalise_symbol_name(w) for w in knowledge]
    out: list[str] = []
    for word in drawing:
        key = normalise_symbol_name(word)
        if any(k and k in key for k in known):
            out.append(word)
    return tuple(out)


def union(drawing: tuple[str, ...], knowledge: tuple[str, ...]) -> tuple[str, ...]:
    out = list(drawing)
    seen = {normalise_symbol_name(w) for w in drawing}
    for word in knowledge:
        if normalise_symbol_name(word) not in seen:
            out.append(word)
    return tuple(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    knowledge_table = load_alias_table(KNOWLEDGE_VOCABULARY)
    knowledge_words = knowledge_table.canonical_names
    drawing_words = words_from_pages(args.pdf, (LEGEND_PAGE,))

    print(f"ゴールデンの symbol_count 行: {len(rows)}")
    print(f"図面 {LEGEND_PAGE} ページの語: {len(drawing_words)} / 知識の語: {len(knowledge_words)}\n")

    s1_words = intersection(drawing_words, knowledge_words)
    s2_words = loose_intersection(drawing_words, knowledge_words)
    s3_words = union(drawing_words, knowledge_words)

    print("=== 本測定(S1 交わり)===")
    s1 = table_of(s1_words, "intersection-v1")
    s1_report = report("S1 交わり        ", s1, rows, args.golden)

    print("\n=== 比べる相手 ===")
    report("S2 ゆるい交わり  ", table_of(s2_words, "loose-v1"), rows, args.golden)
    report("S3 和            ", table_of(s3_words, "union-v1"), rows, args.golden)
    report("19周目 図面の文字", table_of(drawing_words, "drawing-v1"), rows, args.golden)
    report("16周目 知識の語彙", knowledge_table, rows, args.golden)

    print("\n=== 対照 ===")
    q0 = s1_report["result"][HIT_CORRECT]
    rng = random.Random(args.seed)
    shuffled = []
    for _ in range(SHUFFLE_ROUNDS):
        pairing = list(range(len(rows)))
        rng.shuffle(pairing)
        shuffled.append(score(s1, rows, pairing=pairing)[HIT_CORRECT])
    average = sum(shuffled) / len(shuffled)
    print(
        f"対照1 でたらめな並べ替え {SHUFFLE_ROUNDS} 回: 平均 {average:.2f} / 最大 {max(shuffled)} / "
        f"本測定 {q0} 以上 {sum(1 for v in shuffled if v >= q0)} 回 / "
        f"しきい値 {q0 / 2:.1f} 未満か: {'はい' if average < q0 / 2 else 'いいえ'}"
    )

    other_words = words_from_pages(args.pdf, (OTHER_PAGE,))
    report(
        f"対照2 {OTHER_PAGE}ページ×知識",
        table_of(intersection(other_words, knowledge_words), "other-intersection-v1"),
        rows,
        args.golden,
    )

    repeats = [
        score(table_of(intersection(words_from_pages(args.pdf, (LEGEND_PAGE,)),
                                    knowledge_words), "x"), rows)[HIT_CORRECT]
        for _ in range(3)
    ]
    print(f"対照3 反復 3 回: 正しく当たった行 {repeats}")


if __name__ == "__main__":
    main()
