"""19周目の測定: 図面そのものの文字から語彙を作り、当て推量・公開基準の語彙と比べる。

**13 周目が失敗したのは「形と名前の対応」であって「語を拾うこと」ではない。**
個数は人が数えると 14 周目に決めたので、対応は要らず語彙だけあればよい。

**拾った語はそのまま印字しない。** 匿名化v2 には事務所名・個人名・
建築士登録番号が取り出せるページがある。出すのは件数と割合だけ。

採点に `trigger_terms` を使うのは 17・18 周目と同じ(`usage_policy` に沿って
生成のあとの採点にだけ使う。抽出の側には渡さない)。

実行::

    .venv/bin/python -m benchmarks.measure_drawing_vocabulary \
        --pdf <図面.pdf> --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import random
import re
from pathlib import Path

from benchmarks.measure_hit_correctness import (
    HIT_CORRECT,
    HIT_UNKNOWN,
    HIT_WRONG,
    SHUFFLE_ROUNDS,
    score,
)
from benchmarks.measure_knowledge_vocabulary import KNOWLEDGE_VOCABULARY
from benchmarks.measure_symbol_aliases import other_rows, reach_table
from benchmarks.measure_symbol_counts import symbol_rows
from estimating.symbol_aliases import (
    AliasTable,
    load_alias_table,
    normalise_symbol_name,
    parse_alias_table,
)

#: 語を拾うページ(1 始まり)。「凡例」の語があるページのうち図形がいちばん多い。
LEGEND_PAGE = 22

#: 対照1 に使う、電気ではないページ(1 始まり)。
OTHER_PAGE = 33

#: 語として受け付ける長さ(正規化したあと)。
MIN_WORD, MAX_WORD = 2, 10

#: 語の切れ目。**空白・記号・句読点・数字で切る。**
_SPLIT = re.compile(r"[^ぁ-んァ-ヶ一-龥A-Za-zＡ-Ｚａ-ｚｦ-ﾟ]+")


def words_from_pages(pdf_path: Path, pages: tuple[int, ...]) -> tuple[str, ...]:
    """**正解を見ない。** ページの文字を、決めた規則で語に切って全部返す。"""
    import pymupdf

    seen: list[str] = []
    known: set[str] = set()
    with pymupdf.open(pdf_path) as document:
        for page_number in pages:
            text = document[page_number - 1].get_text()
            for chunk in _SPLIT.split(text):
                word = normalise_symbol_name(chunk)
                if not (MIN_WORD <= len(word) <= MAX_WORD):
                    continue
                if word.isdigit() or (len(word) == 1):
                    continue
                if word in known:
                    continue
                known.add(word)
                seen.append(word)
    return tuple(seen)


def table_of(words: tuple[str, ...], table_id: str) -> AliasTable:
    return parse_alias_table(
        {"format_version": 1, "table_id": table_id,
         "symbols": [{"canonical": w} for w in words]}
    )


def worst_other_ratio(table: AliasTable, golden: Path) -> tuple[float, dict[str, str]]:
    detail: dict[str, str] = {}
    worst = 0.0
    for kind in ("geometry_derived", "explicit_text", "standard_rule"):
        others = other_rows(golden, kind)
        hit = reach_table(table, others)
        ratio = hit / len(others)
        detail[kind] = f"{hit}/{len(others)} = {ratio:.3f}"
        worst = max(worst, ratio)
    return worst, detail


def report(label: str, table: AliasTable, rows: list[dict], golden: Path) -> dict:
    result = score(table, rows)
    reached = result["届いた"] or 1
    worst, detail = worst_other_ratio(table, golden)
    print(
        f"{label}: 語 {len(table.canonical_names):5d} / 届いた {result['届いた']:2d}/{len(rows)} / "
        f"正しく {result[HIT_CORRECT]:2d}({result[HIT_CORRECT] / reached:.3f}) / "
        f"取り違え {result[HIT_WRONG]:2d} / 判定できない {result[HIT_UNKNOWN]:2d} / "
        f"誤爆いちばん高い {worst:.3f}"
    )
    print(f"        別の種類の行: {detail}")
    return {"result": result, "worst": worst, "detail": detail}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    rows = symbol_rows(args.golden)
    print(f"ゴールデンの symbol_count 行: {len(rows)}\n")

    legend_words = words_from_pages(args.pdf, (LEGEND_PAGE,))
    legend = table_of(legend_words, "drawing-page22-v1")
    print("=== 本測定 ===")
    main_report = report(f"22ページの文字   ", legend, rows, args.golden)

    print("\n=== 比べる相手(16周目・15周目) ===")
    knowledge = load_alias_table(KNOWLEDGE_VOCABULARY)
    report("知識の語彙(61語)", knowledge, rows, args.golden)

    print("\n=== 対照 ===")
    other = table_of(words_from_pages(args.pdf, (OTHER_PAGE,)), "drawing-page33-v1")
    report(f"対照1 {OTHER_PAGE}ページの文字", other, rows, args.golden)

    every = table_of(words_from_pages(args.pdf, tuple(range(1, 35))), "drawing-all-v1")
    report("対照2 34ページ全部  ", every, rows, args.golden)

    rng = random.Random(args.seed)
    q0 = main_report["result"][HIT_CORRECT]
    shuffled = []
    for _ in range(SHUFFLE_ROUNDS):
        pairing = list(range(len(rows)))
        rng.shuffle(pairing)
        shuffled.append(score(legend, rows, pairing=pairing)[HIT_CORRECT])
    average = sum(shuffled) / len(shuffled)
    print(
        f"対照3 でたらめな並べ替え {SHUFFLE_ROUNDS} 回: 平均 {average:.2f} / 最大 {max(shuffled)} / "
        f"本測定 {q0} 以上になった回数 {sum(1 for v in shuffled if v >= q0)}"
    )
    print(f"      しきい値 {q0 / 2:.1f} 未満か: {'はい' if average < q0 / 2 else 'いいえ'}")

    repeats = [
        score(table_of(words_from_pages(args.pdf, (LEGEND_PAGE,)), "x"), rows)[HIT_CORRECT]
        for _ in range(3)
    ]
    print(f"対照4 反復 3 回: 正しく当たった行 {repeats}")


if __name__ == "__main__":
    main()
