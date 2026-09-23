"""31周目の測定: 性質を組み合わせたら上限はどこまで上がるか。

基準は `docs/a2_word_separability_combined_criteria.md`(測る前にコミット済み)。

**この測定は上限を測るためのもので、使える規則は 1 つも生まない。**
しきい値も性質の組み合わせも正解を見て選んでいる。

**出すのは件数と割合としきい値と性質の名前だけ。**

実行::

    .venv/bin/python -m benchmarks.measure_word_separability_combined \
        --golden <採点用.json> --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import random
import re
import unicodedata
from pathlib import Path

import fitz

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.schedule_tables import read_finish_schedules

AREA_KIND = "geometry_derived"
TOKEN = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")
RECALL_FLOOR = 0.50
DECIDE = 0.50


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def oracle_words(golden: Path) -> set[str]:
    payload = json.loads(golden.read_text(encoding="utf-8"))
    return {
        _norm(str(t))
        for row in payload["expected_items"]
        if row.get("expected_source_type") == AREA_KIND
        for t in (row.get("trigger_terms") or [])
        if _norm(str(t))
    }


def build_properties(pdf: Path):
    with fitz.open(pdf) as doc:
        page_count = len(doc)
        per_page = [TOKEN.findall(_norm(page.get_text())) for page in doc]
    universe = sorted({w for page in per_page for w in page})
    page_sets = [set(page) for page in per_page]

    pages_of = {w: sum(1 for s in page_sets if w in s) for w in universe}
    length_of = {w: len(w) for w in universe}
    first_of = {w: next(n for n, s in enumerate(page_sets, start=1) if w in s)
                for w in universe}
    counters = [collections.Counter(page) for page in per_page]
    max_of = {w: max(c[w] for c in counters) for w in universe}

    cells: collections.Counter[str] = collections.Counter()
    for index in range(page_count):
        for table in find_tables(pdf, index):
            for row in table.texts():
                for cell in row:
                    for w in set(TOKEN.findall(_norm(cell))):
                        cells[w] += 1
    cells_of = {w: cells.get(w, 0) for w in universe}

    schedule: set[str] = set()
    for index in range(page_count):
        for s in read_finish_schedules(pdf, index):
            for row in s.rows:
                for value in (row.part, row.finish, row.room):
                    if value:
                        schedule |= set(TOKEN.findall(_norm(value)))
    schedule_of = {w: 1 if w in schedule else 0 for w in universe}

    return universe, [
        ("1 ページ数", pages_of, True),
        ("2 文字数", length_of, True),
        ("3 表の升目", cells_of, True),
        ("4 仕上表の欄", schedule_of, True),
        ("5 最初のページ", first_of, False),
        ("6 1ページ内の最大", max_of, True),
    ]


def keep_sets(universe, values, higher: bool):
    """しきい値ごとの「残る語の集合」を先に作っておく。"""
    out = []
    for threshold in sorted({values[w] for w in universe}):
        if higher:
            kept = frozenset(w for w in universe if values[w] >= threshold)
        else:
            kept = frozenset(w for w in universe if values[w] <= threshold)
        if kept:
            out.append((threshold, kept))
    return out


def best_over(all_keeps, positives: set[str], sizes) -> dict:
    best = {"precision": 0.0, "recall": 0.0, "kept": 0, "combo": None}
    n_pos = len(positives)
    if not n_pos:
        return best
    for size in sizes:
        for combo in itertools.combinations(range(len(all_keeps)), size):
            for picks in itertools.product(*(all_keeps[i][1] for i in combo)):
                kept = picks[0][1]
                for _, other in picks[1:]:
                    kept = kept & other
                    if not kept:
                        break
                if not kept:
                    continue
                got = len(kept & positives)
                if got / n_pos < RECALL_FLOOR:
                    continue
                precision = got / len(kept)
                if precision > best["precision"]:
                    best = {
                        "precision": precision,
                        "recall": got / n_pos,
                        "kept": len(kept),
                        "combo": tuple(
                            (all_keeps[i][0], picks[k][0])
                            for k, i in enumerate(combo)
                        ),
                    }
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    universe, properties = build_properties(args.pdf)
    positives = oracle_words(args.golden) & set(universe)
    print(f"母集団: {len(universe)} / 当たりの集合: {len(positives)}"
          f" / もとの割合: {len(positives) / len(universe):.3f}")

    all_keeps = [(label, keep_sets(universe, values, higher))
                 for label, values, higher in properties]
    for label, keeps in all_keeps:
        print(f"   {label}: しきい値 {len(keeps)} 通り")

    print("\n=== 上限(2 つまたは 3 つを AND) ===")
    for sizes, name in (((2,), "2 つ"), ((3,), "3 つ")):
        best = best_over(all_keeps, positives, sizes)
        combo = " かつ ".join(f"{a}≷{b}" for a, b in (best["combo"] or ()))
        print(f"   {name}: 適合率 {best['precision']:.3f} / 再現率 {best['recall']:.3f}"
              f" / 残る語数 {best['kept']} / 使った性質 [{combo}]")

    best_all = best_over(all_keeps, positives, (2, 3))
    print(f"\n上限(2 つと 3 つの中でいちばん良い点): {best_all['precision']:.3f}")

    print("\n=== 対照 ===")
    rng = random.Random(args.seed)
    fakes = []
    for _ in range(5):
        fake = set(rng.sample(universe, len(positives)))
        fakes.append(round(best_over(all_keeps, fake, (2, 3))["precision"], 3))
    print(f"対照1 でたらめな 26 語で同じ探索 × 5 回: {fakes}")
    beaten = all(best_all["precision"] > f for f in fakes)
    print(f"   本物 {best_all['precision']:.3f} が 5 回すべてを上回るか: "
          f"{'はい' if beaten else 'いいえ'}")

    repeats = [round(best_over(all_keeps, oracle_words(args.golden) & set(universe),
                               (2, 3))["precision"], 3) for _ in range(3)]
    print(f"対照2 反復 3 回: {repeats}")

    print("\n=== 判定 ===")
    if best_all["precision"] >= DECIDE:
        print(f"上限 {best_all['precision']:.3f} ≧ {DECIDE} → "
              "**組み合わせれば分けられる(上限として)。32 周目で作り直す。**")
    else:
        print(f"上限 {best_all['precision']:.3f} < {DECIDE} → "
              "**『図面からは選べない』で決着。**")


if __name__ == "__main__":
    main()
