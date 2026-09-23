"""30周目の測定(2 回目のキラークエスチョン): その 32 語は図面の側の性質で見分けられるか。

基準は `docs/a2_word_separability_criteria.md`(測る前にコミット済み)。

**この測定は上限を測るためのもので、使える規則は 1 つも生まない。**
しきい値は正解を見て選んでいる。「分かれていない」と出たときだけ、そのまま
結論にしてよい(上限で分けられないなら、上限より弱い規則でも分けられない)。

**出すのは件数と割合としきい値だけ。** 語そのもの・図面の文字・室名・寸法・数量は
1 文字も印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_word_separability \
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

from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.schedule_tables import read_finish_schedules

AREA_KIND = "geometry_derived"
TOKEN = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")

#: 判定の境目。**測る前に決めた。**
RECALL_FLOOR = 0.50
SEPARATED = 0.50
NOT_SEPARATED = 0.15


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


def best_precision(values: dict[str, float], positives: set[str], *, higher_is_better: bool) -> dict:
    """しきい値を全部試して、再現率 RECALL_FLOOR 以上のうち適合率が最大の点を返す。"""
    universe = set(values)
    pos = positives & universe
    if not pos:
        return {"precision": 0.0, "recall": 0.0, "threshold": None, "kept": 0}
    candidates = sorted({v for v in values.values()})
    best = {"precision": 0.0, "recall": 0.0, "threshold": None, "kept": 0}
    for threshold in candidates:
        if higher_is_better:
            kept = {w for w, v in values.items() if v >= threshold}
        else:
            kept = {w for w, v in values.items() if v <= threshold}
        if not kept:
            continue
        got = len(kept & pos)
        recall = got / len(pos)
        if recall < RECALL_FLOOR:
            continue
        precision = got / len(kept)
        if precision > best["precision"]:
            best = {"precision": precision, "recall": recall,
                    "threshold": threshold, "kept": len(kept)}
    return best


def verdict_of(precision: float) -> str:
    if precision >= SEPARATED:
        return "分かれている"
    if precision >= NOT_SEPARATED:
        return "弱い手がかり"
    return "分かれていない"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
        per_page = [TOKEN.findall(_norm(page.get_text())) for page in doc]

    universe = {w for page in per_page for w in page}
    positives = oracle_words(args.golden) & universe
    base = len(positives) / len(universe)
    print(f"母集団(2 文字以上の語): {len(universe)}")
    print(f"当たりの集合(正解の手がかり語と完全に一致): {len(positives)}")
    print(f"もとの割合: {base:.3f}")

    pages_of: dict[str, int] = {
        w: sum(1 for page in per_page if w in page) for w in universe
    }
    length_of = {w: len(w) for w in universe}
    first_page_of = {
        w: next(n for n, page in enumerate(per_page, start=1) if w in page)
        for w in universe
    }
    max_in_page_of = {
        w: max(collections.Counter(page)[w] for page in per_page) for w in universe
    }

    cell_counts: collections.Counter[str] = collections.Counter()
    for index in range(page_count):
        for table in find_tables(args.pdf, index):
            for row in table.texts():
                for cell in row:
                    for w in set(TOKEN.findall(_norm(cell))):
                        cell_counts[w] += 1
    in_cells = {w: float(cell_counts.get(w, 0)) for w in universe}

    schedule_words: set[str] = set()
    for index in range(page_count):
        for schedule in read_finish_schedules(args.pdf, index):
            for row in schedule.rows:
                for value in (row.part, row.finish, row.room):
                    if value:
                        schedule_words |= set(TOKEN.findall(_norm(value)))
    in_schedule = {w: 1.0 if w in schedule_words else 0.0 for w in universe}

    properties = [
        ("1 何ページに出るか", pages_of, True),
        ("2 何文字か", length_of, True),
        ("3 表の升目にいくつ出るか", in_cells, True),
        ("4 仕上表の欄と一致するか", in_schedule, True),
        ("5 最初に出るページ", first_page_of, False),
        ("6 1 ページ内の最大出現回数", max_in_page_of, True),
    ]

    print("\n=== 性質ごとの上限(しきい値は全部試して一番良い点) ===")
    print("性質 / 残る語数 / 拾えた割合 / 当たりの濃さ / 判定")
    results = []
    for label, values, higher in properties:
        best = best_precision(values, positives, higher_is_better=higher)
        v = verdict_of(best["precision"])
        results.append({"label": label, **best, "verdict": v})
        print(f"   {label} / {best['kept']:>4} 語 / {best['recall']:.3f}"
              f" / {best['precision']:.3f} / {v}")

    print("\n=== 対照 ===")
    rng = random.Random(args.seed)
    pool = sorted(universe)
    fake_best = []
    for _ in range(10):
        fake = set(rng.sample(pool, len(positives)))
        worst = max(
            best_precision(values, fake, higher_is_better=higher)["precision"]
            for _, values, higher in properties
        )
        fake_best.append(round(worst, 3))
    real_best = max(r["precision"] for r in results)
    print(f"対照1 でたらめに同じ語数を選んで同じ測り方 × 10 回: {fake_best}")
    print(f"   本物のいちばん良い適合率 {real_best:.3f} を上回った回: "
          f"{sum(1 for f in fake_best if f >= real_best)} / 10")

    repeats = [
        round(max(best_precision(values, oracle_words(args.golden) & universe,
                                 higher_is_better=higher)["precision"]
                  for _, values, higher in properties), 3)
        for _ in range(3)
    ]
    print(f"対照2 反復 3 回: {repeats}")

    print("\n=== まとめ ===")
    top = max(results, key=lambda r: r["precision"])
    print(f"いちばん良い性質: {top['label']} / 当たりの濃さ {top['precision']:.3f}"
          f" / 判定 {top['verdict']}")
    if all(r["verdict"] == "分かれていない" for r in results):
        print("**6 つとも分かれていない。答えは『図面からは選べない』。**")


if __name__ == "__main__":
    main()
