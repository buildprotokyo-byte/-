"""25周目の測定: 面積の行のための語彙を、正解を見ていない出どころから作ると何が変わるか。

基準は `docs/a2_knowledge_area_vocabulary_criteria.md`(測る前にコミット済み)。

**出すのは件数と割合だけ。** 実案件の品目名・手がかり語・数量は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_knowledge_area_vocabulary --golden <採点用.json>
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path

VOCAB = Path("estimating/examples/knowledge_area_vocabulary.json")

#: 24周目に使った 4 語。**対照 2(再現の確認)のためだけに持っている。**
ROUND24_WORDS: tuple[str, ...] = ("床", "壁", "天井", "周")

#: 9 周目の経路が答えられる単位。
ANSWERABLE_UNITS: tuple[str, ...] = ("㎡", "m")

OTHER_KINDS = ("symbol_count", "explicit_text", "standard_rule")


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


def matching_words(row: dict, words) -> list[str]:
    haystack = terms_of(row)
    return [w for w in words if w and w in haystack]


def strip_suffix(name: str, suffixes) -> str:
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    payload = json.loads(VOCAB.read_text(encoding="utf-8"))
    items = payload["items"]
    suffixes = tuple(payload["strippable_suffixes"])

    names_a = tuple(_norm(i["name"]) for i in items)
    names_b = tuple(strip_suffix(_norm(i["name"]), suffixes) for i in items)
    unit_of_a = {_norm(i["name"]): i.get("unit") for i in items}
    unit_of_b = {}
    for item in items:
        stem = strip_suffix(_norm(item["name"]), suffixes)
        # 同じ語幹に単位の違う品目が乗ったら、単位は決められないので None にする。
        if stem in unit_of_b and unit_of_b[stem] != item.get("unit"):
            unit_of_b[stem] = None
        else:
            unit_of_b.setdefault(stem, item.get("unit"))

    print(f"語彙: 品目 {len(items)} 件 / 候補1a {len(set(names_a))} 語 / 候補1b {len(set(names_b))} 語")

    area = rows_of(args.golden, "geometry_derived")
    print(f"面積の行(geometry_derived): {len(area)}")

    for label, words, unit_map in (("1a", names_a, unit_of_a), ("1b", names_b, unit_of_b)):
        hit_rows = [r for r in area if hits(r, words)]
        y1 = len(hit_rows)
        base = y1 / len(area)
        print(f"\n=== 候補 {label} ===")
        print(f"Y1{label} 当たる面積の行: {y1} / {len(area)} = {base:.3f}")

        worst = 0.0
        for kind in OTHER_KINDS:
            others = rows_of(args.golden, kind)
            hit = sum(1 for r in others if hits(r, words))
            ratio = hit / len(others)
            worst = max(worst, ratio)
            print(f"   誤爆 {kind}: {hit} / {len(others)} = {ratio:.3f}")
        half = base / 2
        print(f"Y2{label} いちばん高い誤爆 {worst:.3f} < 半分 {half:.3f} か: "
              f"{'はい' if worst < half else 'いいえ'}")

        judged = 0
        agreed = 0
        for row in hit_rows:
            # **単位は NFKC にかけない。** NFKC は「㎡」を「m2」に変えてしまい、
            # 24周目と違うものを測ることになる(対照2がこの取り違えを捕まえた)。
            row_unit = str(row.get("unit") or "")
            units = {unit_map.get(w) for w in matching_words(row, words)}
            units.discard(None)
            if not units:
                continue
            judged += 1
            if any(str(u) == row_unit for u in units):
                agreed += 1
        rate = f"{agreed / judged:.3f}" if judged else "測れない"
        print(f"Y3{label} 単位の一致: {agreed} / {judged}(単位の分かる語が当たった行)= {rate}")

        y4 = sum(1 for r in hit_rows if str(r.get("unit") or "") in ANSWERABLE_UNITS)
        print(f"Y4{label} そのうち単位が ㎡ か m の行: {y4} / {y1}")

    print("\n=== 対照 ===")
    # **24周目の X2 は「単位が ㎡ か m の行」に絞ったうえでの数である。**
    # 最初に書いたこのスクリプトは絞らずに数えていて、同じ 4 語で 12 / 40 が出た。
    # 24周目の報告にも「(参考)単位を問わず語で当たる行」として 12 / 40 = 0.300 が
    # 載っている。**測っていたものが違っただけで、中身は食い違っていない。**
    # 基準に書いたとおりの数(= 24周目の X2)と並べて両方出す。
    r24_any = sum(1 for r in area if hits(r, ROUND24_WORDS))
    r24_x2 = sum(
        1 for r in area
        if hits(r, ROUND24_WORDS) and str(r.get("unit") or "") in ANSWERABLE_UNITS
    )
    std = rows_of(args.golden, "standard_rule")
    r24_std = sum(1 for r in std if hits(r, ROUND24_WORDS))
    print(f"対照2 24周目の 4 語: 単位で絞った X2 {r24_x2} / {len(area)} / "
          f"絞らない場合 {r24_any} / {len(area)} / standard_rule "
          f"{r24_std} / {len(std)} = {r24_std / len(std):.3f}")
    print(f"   再現したか: "
          f"{'はい' if r24_x2 == 10 and abs(r24_std / len(std) - 0.375) < 1e-9 else 'いいえ'}")

    rng = random.Random(args.seed)
    shuffled_counts = []
    for _ in range(10):
        fake = []
        for name in names_a:
            chars = list(name)
            rng.shuffle(chars)
            fake.append("".join(chars))
        shuffled_counts.append(sum(1 for r in area if hits(r, tuple(fake))))
    print(f"対照3 文字を並べ替えた語彙 10 回: {shuffled_counts}")

    repeats = [sum(1 for r in rows_of(args.golden, "geometry_derived") if hits(r, names_a))
               for _ in range(3)]
    print(f"対照4 反復 3 回(候補1a): {repeats}")

    # ------------------------------------------------------------------
    # ここから下は**採否には使わない診断**である。26周目の基準を書く材料。
    # 語彙が当たらなかったのが「語が長すぎる(粒度の問題)」なのか
    # 「そもそも別の語の世界にいる(語彙の問題)」なのかを分ける。
    # ------------------------------------------------------------------
    print("\n=== 診断(採否には使わない) ===")
    term_counts = [len(r.get("trigger_terms") or []) for r in area]
    term_lengths = [len(_norm(str(t))) for r in area for t in (r.get("trigger_terms") or [])]
    print(f"行あたりの手がかり語の数: 最小 {min(term_counts)} / 中央 "
          f"{sorted(term_counts)[len(term_counts) // 2]} / 最大 {max(term_counts)}")
    print(f"手がかり語の文字数: 最小 {min(term_lengths)} / 中央 "
          f"{sorted(term_lengths)[len(term_lengths) // 2]} / 最大 {max(term_lengths)}")
    print(f"語彙側の文字数: 最小 {min(len(n) for n in names_a)} / 中央 "
          f"{sorted(len(n) for n in names_a)[len(names_a) // 2]} / 最大 "
          f"{max(len(n) for n in names_a)}")

    for size in (2, 3, 4):
        grams = tuple({n[i : i + size] for n in names_a for i in range(len(n) - size + 1)})
        reached = sum(1 for r in area if hits(r, grams))
        worst_kind, worst_ratio = "", 0.0
        for kind in OTHER_KINDS:
            others = rows_of(args.golden, kind)
            ratio = sum(1 for r in others if hits(r, grams)) / len(others)
            if ratio > worst_ratio:
                worst_kind, worst_ratio = kind, ratio
        base = reached / len(area)
        print(
            f"語彙を {size} 文字に刻むと: 面積 {reached} / {len(area)} = {base:.3f}"
            f" / いちばん高い誤爆 {worst_kind} {worst_ratio:.3f}"
            f" / 半分 {base / 2:.3f} 未満か: {'はい' if worst_ratio < base / 2 else 'いいえ'}"
            f"(刻んだ語 {len(grams)} 個)"
        )

    # **上限の測定。** 面積の行の手がかり語そのものを語彙にしたら、
    # 別の種類の行にどれだけ当たってしまうか。
    # **この語彙は使えない**(正解から作った語彙なので、当たって当たり前)。
    # 知りたいのは「語で種類を見分けられるか」の天井だけである。件数しか出さない。
    oracle = tuple({_norm(str(t)) for r in area for t in (r.get("trigger_terms") or [])})
    print(f"\n上限: 面積の行の手がかり語そのものを語彙にすると({len(oracle)} 語)")
    print(f"   面積: {sum(1 for r in area if hits(r, oracle))} / {len(area)} = 1.000(当然)")
    for kind in OTHER_KINDS:
        others = rows_of(args.golden, kind)
        hit = sum(1 for r in others if hits(r, oracle))
        print(f"   誤爆 {kind}: {hit} / {len(others)} = {hit / len(others):.3f}")


if __name__ == "__main__":
    main()
