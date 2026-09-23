"""29周目の測定: 語彙の出どころを「回数」から「場所」(仕上表のどの欄か)に変える。

基準は `docs/a2_finish_schedule_vocabulary_criteria.md`(測る前にコミット済み)。

**前半で 1 つ選び、後半で採否を決める。** 分け方の種は 28 周目と同じ。

**出すのは件数と割合だけ。** 選んだ語そのもの・図面の文字・室名・寸法・数量は
1 文字も印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_finish_schedule_vocabulary \
        --golden <採点用.json> --pdf <匿名化v2.pdf>
"""

from __future__ import annotations

import argparse
import json
import random
import re
import unicodedata
from pathlib import Path

import fitz

from axes.image_axis.schedule_tables import read_finish_schedules

AREA_KIND = "geometry_derived"
OTHER_KINDS = ("symbol_count", "explicit_text", "standard_rule")

TOKEN = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")

SPLIT_SEED = 20260928
REACH_FLOOR = 0.250
ROUND24_WORDS: tuple[str, ...] = ("床", "壁", "天井", "周")
ANSWERABLE_UNITS: tuple[str, ...] = ("㎡", "m")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def terms_of(row: dict) -> str:
    return _norm(" ".join(str(t) for t in (row.get("trigger_terms") or [])))


def hits(row: dict, words) -> bool:
    haystack = terms_of(row)
    return any(w and w in haystack for w in words)


def reach(words, rows) -> int:
    return sum(1 for r in rows if hits(r, words))


def split_rows(golden: Path) -> tuple[list[dict], list[dict]]:
    payload = json.loads(golden.read_text(encoding="utf-8"))
    rows = list(payload["expected_items"])
    random.Random(SPLIT_SEED).shuffle(rows)
    half = len(rows) // 2
    return rows[:half], rows[half:]


def of_kind(rows, kind: str) -> list[dict]:
    return [r for r in rows if r.get("expected_source_type") == kind]


def schedule_vocabularies(pdf: Path) -> dict[str, tuple[str, ...]]:
    """仕上表の欄の役割ごとに語を集める。**文字数の足切りはしない。**"""
    parts: set[str] = set()
    finishes: set[str] = set()
    rooms: set[str] = set()
    with fitz.open(pdf) as doc:
        page_count = len(doc)
    for index in range(page_count):
        for schedule in read_finish_schedules(pdf, index):
            for row in schedule.rows:
                if row.part:
                    parts.add(_norm(row.part))
                if row.finish:
                    finishes.add(_norm(row.finish))
                if row.room:
                    rooms.add(_norm(row.room))
    parts.discard("")
    finishes.discard("")
    rooms.discard("")
    return {
        "D1 部位の欄": tuple(sorted(parts)),
        "D2 仕上の欄": tuple(sorted(finishes)),
        "D3 部位+仕上": tuple(sorted(parts | finishes)),
        "D4 室名の欄": tuple(sorted(rooms)),
    }


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
        "ratio": (worst / base) if base else None,
        "control1": base >= REACH_FLOOR and worst < base / 2,
        "W3": sum(1 for r in area
                  if hits(r, words) and str(r.get("unit") or "") in ANSWERABLE_UNITS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    vocabs = schedule_vocabularies(args.pdf)
    first, second = split_rows(args.golden)
    print(f"正解の行を種 {SPLIT_SEED} で 2 つに割った: 前半 {len(first)} / 後半 {len(second)}")
    for label, words in vocabs.items():
        print(f"   {label}: {len(words)} 語")

    print("\n=== 前半(ここで 1 つ選ぶ) ===")
    print("候補 / 語数 / 当たる面積の行 / いちばん高い誤爆 / 誤爆÷当たり / 対照1")
    scored = []
    for label, words in vocabs.items():
        result = score(words, first)
        scored.append({"label": label, "words": words, **result})
        ratio = f"{result['ratio']:.2f}" if result["ratio"] is not None else "-"
        print(f"   {label} / {result['W0']:>3} 語 / {result['W1']:>2} / {result['area']}"
              f" = {result['base']:.3f} / {result['worst_kind'] or '-'} {result['worst']:.3f}"
              f" / {ratio} / {'◯' if result['control1'] else '×'}")

    passing = [s for s in scored if s["control1"]]
    if not passing:
        print("\n前半で対照1を通る候補が 1 つも無い。"
              "**基準のとおり『表の欄でも分けられない』で終わりにする。**")
        return

    passing.sort(key=lambda s: (-s["W1"], s["W0"]))
    chosen = passing[0]
    print(f"\n選んだ候補: {chosen['label']}({chosen['W0']} 語)")

    print("\n=== 後半(ここで採否を決める) ===")
    result = score(chosen["words"], second)
    ratio = f"{result['ratio']:.2f}" if result["ratio"] is not None else "-"
    print(f"U0 語数: {result['W0']}")
    print(f"U1 当たる面積の行: {result['W1']} / {result['area']} = {result['base']:.3f}")
    print(f"U2 いちばん高い誤爆: {result['worst_kind'] or '-'} {result['worst']:.3f}")
    print(f"U3 そのうち単位が ㎡ か m: {result['W3']} / {result['W1']}")
    print(f"U4 誤爆÷当たり: {ratio}"
          f"(28周目の格子は 0.56〜0.77 / 天井の 32 語は 0.29)")

    print("\n=== 対照(すべて後半で) ===")
    c1 = result["control1"]
    print(f"対照1 当たり ≧ {REACH_FLOOR} かつ 誤爆 < 当たり÷2: {'◯' if c1 else '×'}")

    with fitz.open(args.pdf) as doc:
        pool = sorted({w for page in doc for w in TOKEN.findall(_norm(page.get_text()))})
    rng = random.Random(args.seed)
    fakes = []
    for _ in range(10):
        n = min(result["W0"], len(pool))
        fakes.append(reach(tuple(rng.sample(pool, n)), of_kind(second, AREA_KIND)))
    c2 = all(result["W1"] > f for f in fakes)
    print(f"対照2 同じ語数をでたらめに 10 回: {fakes} / 本物 {result['W1']}"
          f" が全部を上回るか: {'◯' if c2 else '×'}")

    rooms = score(vocabs["D4 室名の欄"], second)
    c3 = result["W1"] > rooms["W1"]
    print(f"対照3 D4 室名の欄を同じ後半に当てる: {rooms['W1']} / {rooms['area']}"
          f" / 選んだ候補 {result['W1']} が上回るか: {'◯' if c3 else '×'}")

    x2_first = sum(1 for r in of_kind(first, AREA_KIND)
                   if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS)
    x2_second = sum(1 for r in of_kind(second, AREA_KIND)
                    if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS)
    c4 = x2_first + x2_second == 10
    print(f"対照4 24周目の 4 語: 前半 {x2_first} + 後半 {x2_second} = {x2_first + x2_second}"
          f" / 10 と一致するか: {'◯' if c4 else '×'}")

    repeats = [score(chosen["words"], split_rows(args.golden)[1])["W1"] for _ in range(3)]
    c5 = len(set(repeats)) == 1
    print(f"対照5 反復 3 回: {repeats} / {'◯' if c5 else '×'}")

    verdict = (
        "採用" if result["base"] > REACH_FLOOR and c1 and c2 and c3 and c5
        else "保留" if result["base"] > REACH_FLOOR else "不採用"
    )
    print(f"\n判定: U1 の割合 {result['base']:.3f} → **{verdict}**")


if __name__ == "__main__":
    main()
