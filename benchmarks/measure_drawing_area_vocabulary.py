"""27周目の測定: 面積の行の語彙を、図面の文字だけから作る。

基準は `docs/a2_drawing_area_vocabulary_criteria.md`(測る前にコミット済み)。

**正解ファイルは採点にしか使わない。** 語彙は図面の文字だけから作る。

**出すのは件数と割合だけ。** 選んだ語そのもの・図面の文字・室名・寸法・数量は
1 文字も印字しない。匿名化 v2 には塗りつぶされて見えない文字データが残っている。

実行::

    .venv/bin/python -m benchmarks.measure_drawing_area_vocabulary \
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

AREA_KIND = "geometry_derived"
OTHER_KINDS = ("symbol_count", "explicit_text", "standard_rule")

#: 語の切り出し方。**測る前に決めた。** 数字を含む語は最初から入らない。
TOKEN = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}")

#: 足切りの文字数。26周目に 2 文字の語は並べ替えても図面に現れると分かったため。
MIN_LENGTH = 3

#: 24周目に使った 4 語。**対照 3(再現の確認)のためだけに持っている。**
ROUND24_WORDS: tuple[str, ...] = ("床", "壁", "天井", "周")

ANSWERABLE_UNITS: tuple[str, ...] = ("㎡", "m")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def rows_of(golden: Path, kind: str) -> list[dict]:
    payload = json.loads(golden.read_text(encoding="utf-8"))
    return [i for i in payload["expected_items"] if i.get("expected_source_type") == kind]


def terms_of(row: dict) -> str:
    return _norm(" ".join(str(t) for t in (row.get("trigger_terms") or [])))


def hits(row: dict, words) -> bool:
    haystack = terms_of(row)
    return any(w and w in haystack for w in words)


def reach(words, rows) -> int:
    return sum(1 for r in rows if hits(r, words))


def words_in(text: str) -> set[str]:
    return {w for w in TOKEN.findall(_norm(text)) if len(w) >= MIN_LENGTH}


def page_words(pdf: Path) -> list[set[str]]:
    with fitz.open(pdf) as doc:
        return [words_in(page.get_text()) for page in doc]


def table_words(pdf: Path, page_count: int) -> set[str]:
    found: set[str] = set()
    for index in range(page_count):
        for table in find_tables(pdf, index):
            for row in table.texts():
                for cell in row:
                    found |= words_in(cell)
    return found


def judge(label: str, words, golden: Path, area) -> dict:
    got = reach(words, area)
    base = got / len(area)
    worst_kind, worst = "", 0.0
    for kind in OTHER_KINDS:
        others = rows_of(golden, kind)
        ratio = reach(words, others) / len(others)
        if ratio > worst:
            worst_kind, worst = kind, ratio
    w3 = sum(
        1 for r in area
        if hits(r, words) and str(r.get("unit") or "") in ANSWERABLE_UNITS
    )
    control1 = base > 0 and worst < base / 2
    print(f"\n=== 候補 {label} ===")
    print(f"W0 語数: {len(words)}")
    print(f"W1 当たる面積の行: {got} / {len(area)} = {base:.3f}")
    print(f"W2 いちばん高い誤爆: {worst_kind or '-'} {worst:.3f}"
          f" / 半分 {base / 2:.3f} 未満か: {'はい' if control1 else 'いいえ'}")
    print(f"W3 そのうち単位が ㎡ か m: {w3} / {got}")
    return {"label": label, "W0": len(words), "W1": got, "base": base,
            "W2": {"kind": worst_kind, "ratio": worst}, "W3": w3, "control1": control1}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    per_page = page_words(args.pdf)
    all_words = set().union(*per_page) if per_page else set()
    seen = collections.Counter(w for page in per_page for w in page)
    multi_page = {w for w, n in seen.items() if n >= 2}
    in_tables = table_words(args.pdf, len(per_page))

    print(f"図面: {len(per_page)} ページ")
    print(f"切り出せた語({MIN_LENGTH} 文字以上): {len(all_words)}")

    area = rows_of(args.golden, AREA_KIND)
    print(f"面積の行: {len(area)}")

    results = [
        judge("A 表の升目の中だけ", tuple(sorted(in_tables)), args.golden, area),
        judge("B 2 ページ以上に出る語", tuple(sorted(multi_page)), args.golden, area),
        judge("C 絞らない", tuple(sorted(all_words)), args.golden, area),
    ]

    print("\n=== 対照 ===")
    rng = random.Random(args.seed)
    pool = sorted(all_words)
    for item in results:
        if item["W0"] == 0 or item["W0"] > len(pool):
            print(f"対照2 候補 {item['label']}: 測れない(語数 {item['W0']})")
            continue
        fakes = []
        for _ in range(10):
            fakes.append(reach(tuple(rng.sample(pool, item["W0"])), area))
        beaten = all(item["W1"] > f for f in fakes)
        print(f"対照2 候補 {item['label']}: 同じ語数のでたらめ 10 回 {fakes}"
              f" / 本物 {item['W1']} が全部を上回るか: {'はい' if beaten else 'いいえ'}")
        item["control2"] = beaten

    x2 = sum(
        1 for r in area
        if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS
    )
    std = rows_of(args.golden, "standard_rule")
    std_ratio = reach(ROUND24_WORDS, std) / len(std)
    print(f"対照3 24周目の 4 語: X2 {x2} / {len(area)} / standard_rule {std_ratio:.3f}"
          f" / 再現したか: "
          f"{'はい' if x2 == 10 and abs(std_ratio - 0.375) < 1e-9 else 'いいえ'}")

    repeats = [reach(tuple(sorted(in_tables)), rows_of(args.golden, AREA_KIND))
               for _ in range(3)]
    print(f"対照4 反復 3 回(候補 A): {repeats}")

    print("\n=== まとめ ===")
    for item in results:
        verdict = (
            "採用" if item["W1"] > 10 and item["control1"] and item.get("control2")
            else "保留" if item["W1"] > 10 else "不採用"
        )
        print(f"   候補 {item['label']}: W1 {item['W1']} / {len(area)}"
              f" / 対照1 {'◯' if item['control1'] else '×'}"
              f" / 対照2 {'◯' if item.get('control2') else '×'} → **{verdict}**")


if __name__ == "__main__":
    main()
