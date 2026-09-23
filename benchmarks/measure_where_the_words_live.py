"""26周目の測定(キラークエスチョン): 行の呼び名は図面の中にあるのか。

基準は `docs/a2_where_the_words_live_criteria.md`(測る前にコミット済み)。

**この測定からは使える語彙は 1 つも生まれない。** 当てる語は正解ファイルから
取ったものなので、当たって当然である。知りたいのは「その語がどこに住んでいるか」だけ。

**出すのは件数と割合とページ番号だけ。** 語そのもの・図面の文字・室名・寸法・数量は
1 文字も印字しない。匿名化 v2 には塗りつぶされて見えない文字データが残っているため、
取り出した文字列を出力に混ぜない。

実行::

    .venv/bin/python -m benchmarks.measure_where_the_words_live \
        --golden <採点用.json> --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import unicodedata
from pathlib import Path

import fitz

AREA_KIND = "geometry_derived"
OTHER_KINDS = ("symbol_count", "explicit_text", "standard_rule")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def rows_of(golden: Path, kind: str) -> list[dict]:
    payload = json.loads(golden.read_text(encoding="utf-8"))
    return [i for i in payload["expected_items"] if i.get("expected_source_type") == kind]


def terms_of(row: dict) -> str:
    return _norm(" ".join(str(t) for t in (row.get("trigger_terms") or [])))


def vocabulary_of(rows) -> tuple[str, ...]:
    words = {_norm(str(t)) for r in rows for t in (r.get("trigger_terms") or [])}
    return tuple(sorted(w for w in words if w))


def hits(row: dict, words) -> bool:
    haystack = terms_of(row)
    return any(w and w in haystack for w in words)


def page_texts(pdf: Path) -> list[str]:
    with fitz.open(pdf) as doc:
        return [_norm(page.get_text()) for page in doc]


def pages_containing(word: str, texts) -> list[int]:
    """1 始まりのページ番号。**語も本文も返さない。番号だけ。**"""
    return [n for n, text in enumerate(texts, start=1) if word and word in text]


def reach(words, rows) -> int:
    return sum(1 for r in rows if hits(r, words))


def report_reach(label: str, words, golden: Path, area) -> None:
    got = reach(words, area)
    base = got / len(area)
    print(f"   {label}: 面積の行 {got} / {len(area)} = {base:.3f}")
    worst_kind, worst = "", 0.0
    for kind in OTHER_KINDS:
        others = rows_of(golden, kind)
        ratio = reach(words, others) / len(others)
        if ratio > worst:
            worst_kind, worst = kind, ratio
    ok = "はい" if base > 0 and worst < base / 2 else "いいえ"
    print(f"   {label}: いちばん高い誤爆 {worst_kind or '-'} {worst:.3f}"
          f" / 半分 {base / 2:.3f} 未満か: {ok}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    texts = page_texts(args.pdf)
    with_text = sum(1 for t in texts if t.strip())
    print(f"図面: {len(texts)} ページ / 文字のあるページ {with_text}")

    area = rows_of(args.golden, AREA_KIND)
    words = vocabulary_of(area)
    print(f"面積の行: {len(area)} / その手がかり語: {len(words)} 語")

    found = [w for w in words if pages_containing(w, texts)]
    print(f"\nZ1 図面に現れる語: {len(found)} / {len(words)}"
          f" = {len(found) / len(words):.3f}")

    got = reach(tuple(found), area)
    print(f"Z2 図面に現れた語だけで当たる面積の行: {got} / {len(area)}")

    print("Z3 誤爆:")
    report_reach("図面に現れた語だけ", tuple(found), args.golden, area)

    counts = collections.Counter()
    for w in found:
        for n in pages_containing(w, texts):
            counts[n] += 1
    spread = sorted(counts.items())
    print(f"Z4 語が現れたページ: {len(counts)} ページ"
          f" / 1 ページあたりの語数 最小 {min(counts.values()) if counts else 0}"
          f" / 最大 {max(counts.values()) if counts else 0}")
    print(f"   ページ番号と語数: {spread}")

    print("\n=== 対照 ===")
    print("対照2 記号の行の手がかり語で同じことをする(19周目との突き合わせ)")
    sym_rows = rows_of(args.golden, "symbol_count")
    sym_words = vocabulary_of(sym_rows)
    sym_found = [w for w in sym_words if pages_containing(w, texts)]
    print(f"   記号の語: {len(sym_found)} / {len(sym_words)}"
          f" = {len(sym_found) / len(sym_words):.3f} が図面に現れる")
    print(f"   面積の語: {len(found)} / {len(words)}"
          f" = {len(found) / len(words):.3f} が図面に現れる")

    rng = random.Random(args.seed)
    shuffled = []
    for _ in range(10):
        n = 0
        for w in words:
            chars = list(w)
            rng.shuffle(chars)
            if pages_containing("".join(chars), texts):
                n += 1
        shuffled.append(n)
    print(f"対照3 文字を並べ替えた語 10 回: {shuffled}")

    repeats = [len([w for w in vocabulary_of(rows_of(args.golden, AREA_KIND))
                    if pages_containing(w, texts)]) for _ in range(3)]
    print(f"対照4 反復 3 回: {repeats}")

    # ------------------------------------------------------------------
    # 診断(**判定には使わない**)。27周目の基準を書く材料。
    # ------------------------------------------------------------------
    print("\n=== 診断(判定には使わない) ===")

    # (1) 対照3 が 0 にならなかったのは短い語のせいか。文字数ごとに分ける。
    by_len: dict[int, list[str]] = collections.defaultdict(list)
    for w in words:
        by_len[len(w)].append(w)
    print("文字数ごとの、本物の語と並べ替えた語の当たり方:")
    for size in sorted(by_len):
        real = sum(1 for w in by_len[size] if pages_containing(w, texts))
        fake_total = 0
        for _ in range(10):
            for w in by_len[size]:
                chars = list(w)
                rng.shuffle(chars)
                if pages_containing("".join(chars), texts):
                    fake_total += 1
        print(f"   {size} 文字({len(by_len[size])} 語): 本物 {real}"
              f" / 並べ替え 10 回の平均 {fake_total / 10:.1f}")

    # (2) 「図面にある」の分母。図面の文字から機械的に切り出せる語は何語あるか。
    #     ここが大きいほど、「32 語が図面にある」の値打ちは下がる。
    import re

    token = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")
    tokens = {m for text in texts for m in token.findall(text)}
    print(f"図面の文字から機械的に切り出せる語(2 文字以上の仮名漢字の並び): {len(tokens)} 語")
    print(f"   そのうち面積の行の語と完全に一致するもの: "
          f"{len(set(words) & tokens)} / {len(words)}")
    print(f"   記号の行の語と完全に一致するもの: "
          f"{len(set(sym_words) & tokens)} / {len(sym_words)}")


if __name__ == "__main__":
    main()
