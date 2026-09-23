"""21周目: キラークエスチョン「ここまでの選び方は P011 1 件に合わせただけではないのか」。

**2 件目は手元に無い。** 1 件の中で確かめられることを 2 つ測る。

- 測定A … 26 行を偶数番目・奇数番目に割っても、語彙の**順位**が同じか。
- 測定B … 凡例のページを**自動で**選べるか(19 周目は人が選んだ)。

**出すのは件数と割合と順位だけ。** 語そのもの・品目名・ページの中身は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_one_case_killer_question \
        --pdf <図面.pdf> --golden <採点用.json>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.measure_drawing_vocabulary import (
    LEGEND_PAGE,
    table_of,
    words_from_pages,
    worst_other_ratio,
)
from benchmarks.measure_hit_correctness import HIT_CORRECT, HIT_UNKNOWN, HIT_WRONG, score
from benchmarks.measure_knowledge_vocabulary import KNOWLEDGE_VOCABULARY
from benchmarks.measure_symbol_aliases import FOUR_WORDS, SYNTHETIC_ALIASES
from benchmarks.measure_symbol_counts import symbol_rows
from benchmarks.measure_vocabulary_intersection import intersection
from estimating.symbol_aliases import AliasTable, load_alias_table

#: 19 周目に人が使った規則。**同じ規則を機械的に当てて、同じページが出るかを見る。**
LEGEND_WORD = "凡例"


def metrics(table: AliasTable, rows: list[dict], golden: Path | None) -> dict:
    result = score(table, rows)
    reached = result["届いた"]
    judged = result[HIT_CORRECT] + result[HIT_WRONG]
    worst = worst_other_ratio(table, golden)[0] if golden else None
    return {
        "語数": len(table.canonical_names),
        "届いた": reached,
        "間違い率": (result[HIT_WRONG] / judged) if judged else None,
        "判定できない率": (result[HIT_UNKNOWN] / reached) if reached else None,
        "誤爆": worst,
        "正しく": result[HIT_CORRECT],
        "取り違え": result[HIT_WRONG],
    }


def rank(names: list[str], values: list[float | None], *, ascending: bool) -> list[str]:
    """**順位だけ**を返す。同じ値は名前の順で並べる(並べ方を先に決めておく)。"""
    pairs = [(n, v) for n, v in zip(names, values) if v is not None]
    pairs.sort(key=lambda pair: (pair[1] if ascending else -pair[1], pair[0]))
    return [n for n, _ in pairs]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    knowledge = load_alias_table(KNOWLEDGE_VOCABULARY)
    guessed = load_alias_table(SYNTHETIC_ALIASES)
    drawing_words = words_from_pages(args.pdf, (LEGEND_PAGE,))
    drawing = table_of(drawing_words, "drawing-v1")
    inter = table_of(intersection(drawing_words, knowledge.canonical_names), "inter-v1")
    four = table_of(FOUR_WORDS, "four-v1")

    tables = {
        "4語(14周)": four,
        "11語(15周)": guessed,
        "61語(16周)": knowledge,
        "226語(19周)": drawing,
        "8語(20周)": inter,
    }

    print("=== 新しい測り方で 14〜20 周を並べ直す(**判定は動かさない**)===")
    print(f"{'語彙':14} {'語数':>5} {'届いた':>7} {'間違い率':>9} {'判定できない率':>13} {'誤爆':>7}")
    for label, table in tables.items():
        m = metrics(table, rows, args.golden)
        print(
            f"{label:14} {m['語数']:5d} {m['届いた']:4d}/{len(rows)} "
            f"{m['間違い率']:9.3f} {m['判定できない率']:13.3f} {m['誤爆']:7.3f}"
        )

    print("\n=== 測定A: 26 行を偶数番目・奇数番目に割る ===")
    halves = {
        "偶数番目": [row for i, row in enumerate(rows) if i % 2 == 0],
        "奇数番目": [row for i, row in enumerate(rows) if i % 2 == 1],
    }
    reach_rank: dict[str, list[str]] = {}
    wrong_rank: dict[str, list[str]] = {}
    for half_name, half_rows in halves.items():
        names, reached, wrong = [], [], []
        print(f"\n  {half_name}({len(half_rows)} 行)")
        for label, table in tables.items():
            m = metrics(table, half_rows, None)
            names.append(label)
            reached.append(float(m["届いた"]))
            wrong.append(m["間違い率"])
            print(
                f"    {label:14} 届いた {m['届いた']:2d}/{len(half_rows)} / "
                f"間違い率 {m['間違い率'] if m['間違い率'] is not None else float('nan'):.3f}"
            )
        reach_rank[half_name] = rank(names, reached, ascending=False)
        wrong_rank[half_name] = rank(names, wrong, ascending=True)

    print("\n  届いた行の多い順:")
    for half_name, order in reach_rank.items():
        print(f"    {half_name}: {order}")
    same_reach = reach_rank["偶数番目"] == reach_rank["奇数番目"]
    print(f"    → 同じか: {'はい' if same_reach else 'いいえ'}")

    print("\n  間違い率の低い順:")
    for half_name, order in wrong_rank.items():
        print(f"    {half_name}: {order}")
    same_wrong = wrong_rank["偶数番目"] == wrong_rank["奇数番目"]
    print(f"    → 同じか: {'はい' if same_wrong else 'いいえ'}")

    print("\n=== 測定B: 凡例のページを自動で選べるか ===")
    import pymupdf

    candidates: list[tuple[int, int]] = []
    with pymupdf.open(args.pdf) as document:
        for index in range(len(document)):
            page = document[index]
            if LEGEND_WORD in page.get_text():
                candidates.append((index + 1, len(page.get_drawings())))
    candidates.sort(key=lambda pair: (-pair[1], pair[0]))
    print(f"  「{LEGEND_WORD}」の語があるページ(1 始まり)と図形の数: {candidates}")
    chosen = candidates[0][0] if candidates else None
    print(f"  自動で選ばれたページ: {chosen} / 19 周目に人が選んだページ: {LEGEND_PAGE}")
    print(f"  → 一致するか: {'はい' if chosen == LEGEND_PAGE else 'いいえ'}")

    print("\n=== 答え ===")
    verdict = "1 件の中でまだ意味がある" if (same_reach and same_wrong and chosen == LEGEND_PAGE) else "2 件目が要る"
    print(f"  測定A 届いた順 {'同じ' if same_reach else '入れ替わる'} / "
          f"間違い率順 {'同じ' if same_wrong else '入れ替わる'} / "
          f"測定B {'一致' if chosen == LEGEND_PAGE else '一致しない'} → {verdict}")


if __name__ == "__main__":
    main()
