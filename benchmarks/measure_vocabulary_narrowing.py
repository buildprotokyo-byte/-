"""28周目の測定: 語彙をどこまで絞れば行の種類を見分けられるか。

基準は `docs/a2_vocabulary_narrowing_criteria.md`(測る前にコミット済み)。

**前半で 1 つ選び、後半で採否を決める。** 格子を全部試して一番良かったものを
採ると、正解に合わせたことになるため(18周目と同じ手)。

**出すのは件数と割合だけ。** 選んだ語そのもの・図面の文字・室名・寸法・数量は
1 文字も印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_vocabulary_narrowing \
        --golden <採点用.json> --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import unicodedata
from pathlib import Path

import fitz

AREA_KIND = "geometry_derived"
OTHER_KINDS = ("symbol_count", "explicit_text", "standard_rule")

TOKEN = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")

#: 格子。**測る前に決めた。これ以外は試さない。**
PAGE_THRESHOLDS: tuple[int, ...] = (2, 3, 5, 10)
LENGTH_THRESHOLDS: tuple[int, ...] = (3, 4, 5)

#: 正解の行を半分に分ける種。**測る前に決めた。**
SPLIT_SEED = 20260928

ROUND24_WORDS: tuple[str, ...] = ("床", "壁", "天井", "周")
ANSWERABLE_UNITS: tuple[str, ...] = ("㎡", "m")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def terms_of(row: dict) -> str:
    return _norm(" ".join(str(t) for t in (row.get("trigger_terms") or [])))


def hits(row: dict, words) -> bool:
    haystack = terms_of(row)
    return any(w and w in haystack for w in words)


def reach(words, rows) -> int:
    return sum(1 for r in rows if hits(r, words))


def split_rows(golden: Path) -> tuple[list[dict], list[dict]]:
    """正解の行を種を決めた無作為の並べ替えで丸ごと 2 つに割る。"""
    payload = json.loads(golden.read_text(encoding="utf-8"))
    rows = list(payload["expected_items"])
    random.Random(SPLIT_SEED).shuffle(rows)
    half = len(rows) // 2
    return rows[:half], rows[half:]


def of_kind(rows, kind: str) -> list[dict]:
    return [r for r in rows if r.get("expected_source_type") == kind]


def page_words(pdf: Path) -> list[set[str]]:
    with fitz.open(pdf) as doc:
        return [set(TOKEN.findall(_norm(page.get_text()))) for page in doc]


def vocabulary(per_page, min_pages: int, min_length: int) -> tuple[str, ...]:
    seen = collections.Counter(w for page in per_page for w in page)
    return tuple(sorted(
        w for w, n in seen.items() if n >= min_pages and len(w) >= min_length
    ))


def score(words, rows) -> dict:
    area = of_kind(rows, AREA_KIND)
    got = reach(words, area)
    base = got / len(area) if area else 0.0
    worst_kind, worst = "", 0.0
    for kind in OTHER_KINDS:
        others = of_kind(rows, kind)
        if not others:
            continue
        ratio = reach(words, others) / len(others)
        if ratio > worst:
            worst_kind, worst = kind, ratio
    return {
        "W0": len(words), "W1": got, "area": len(area), "base": base,
        "worst_kind": worst_kind, "worst": worst,
        "control1": base > 0 and worst < base / 2,
        "W3": sum(1 for r in area
                  if hits(r, words) and str(r.get("unit") or "") in ANSWERABLE_UNITS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    per_page = page_words(args.pdf)
    first, second = split_rows(args.golden)
    print(f"正解の行を種 {SPLIT_SEED} で 2 つに割った: 前半 {len(first)} / 後半 {len(second)}")
    for label, rows in (("前半", first), ("後半", second)):
        counts = {k: len(of_kind(rows, k)) for k in (AREA_KIND, *OTHER_KINDS)}
        print(f"   {label}: {counts}")

    print("\n=== 前半での格子(ここで 1 つ選ぶ) ===")
    print("ページ数 / 文字数 / 語数 / 当たる面積の行 / いちばん高い誤爆 / 対照1")
    grid = []
    for pages in PAGE_THRESHOLDS:
        for length in LENGTH_THRESHOLDS:
            words = vocabulary(per_page, pages, length)
            result = score(words, first)
            grid.append({"pages": pages, "length": length, "words": words, **result})
            print(f"   {pages:>2} 以上 / {length} 以上 / {result['W0']:>4} 語 /"
                  f" {result['W1']:>2} / {result['area']} = {result['base']:.3f} /"
                  f" {result['worst_kind'] or '-'} {result['worst']:.3f} /"
                  f" {'◯' if result['control1'] else '×'}")

    passing = [g for g in grid if g["control1"]]
    if not passing:
        print("\n前半で対照1を通る点が 1 つも無い。"
              "**基準のとおり『この 2 軸では絞れない』で終わりにする。**")
        return

    passing.sort(key=lambda g: (-g["W1"], g["W0"], -g["pages"]))
    chosen = passing[0]
    print(f"\n選んだ点: ページ {chosen['pages']} 以上 / 文字数 {chosen['length']} 以上"
          f" / 語数 {chosen['W0']}")
    print(f"   (前半での数: {chosen['W1']} / {chosen['area']} = {chosen['base']:.3f}"
          f" / 誤爆 {chosen['worst']:.3f})")

    print("\n=== 後半(ここで採否を決める) ===")
    words = chosen["words"]
    result = score(words, second)
    print(f"V0 語数: {result['W0']}")
    print(f"V1 当たる面積の行: {result['W1']} / {result['area']} = {result['base']:.3f}")
    print(f"V2 いちばん高い誤爆: {result['worst_kind'] or '-'} {result['worst']:.3f}"
          f" / 半分 {result['base'] / 2:.3f} 未満か: {'はい' if result['control1'] else 'いいえ'}")
    print(f"V3 そのうち単位が ㎡ か m: {result['W3']} / {result['W1']}")

    print("\n=== 対照(すべて後半で) ===")
    pool = sorted(set().union(*per_page) if per_page else set())
    pool = [w for w in pool if len(w) >= min(LENGTH_THRESHOLDS)]
    rng = random.Random(args.seed)
    fakes = []
    for _ in range(10):
        n = min(result["W0"], len(pool))
        fakes.append(reach(tuple(rng.sample(pool, n)), of_kind(second, AREA_KIND)))
    beaten = all(result["W1"] > f for f in fakes)
    print(f"対照2 同じ語数をでたらめに 10 回: {fakes} / 本物 {result['W1']}"
          f" が全部を上回るか: {'はい' if beaten else 'いいえ'}")

    x2_first = sum(1 for r in of_kind(first, AREA_KIND)
                   if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS)
    x2_second = sum(1 for r in of_kind(second, AREA_KIND)
                    if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS)
    print(f"対照3 24周目の 4 語: 前半 {x2_first} + 後半 {x2_second} = {x2_first + x2_second}"
          f" / 24周目の X2 10 と一致するか: "
          f"{'はい' if x2_first + x2_second == 10 else 'いいえ'}")

    repeats = [score(words, split_rows(args.golden)[1])["W1"] for _ in range(3)]
    print(f"対照4 反復 3 回: {repeats}")

    verdict = (
        "採用" if result["base"] > 0.250 and result["control1"] and beaten
        else "保留" if result["base"] > 0.250 else "不採用"
    )
    print(f"\n判定: V1 の割合 {result['base']:.3f}"
          f" / 対照1 {'◯' if result['control1'] else '×'}"
          f" / 対照2 {'◯' if beaten else '×'} → **{verdict}**")


if __name__ == "__main__":
    main()
