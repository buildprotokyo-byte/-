"""33周目の測定(トライアルB): 語順を無視する揃え方を、壊しにいく。

基準は `docs/b_item_key_break_criteria.md`(測る前にコミット済み)。

**実案件も正解ファイルも実図面も使わない。** 語はすべて公共建築工事内訳書標準書式の
35 品目の名前から機械的に切り出す。こちらで新しい語を作らない。

**揃える処理はこのスクリプトの中だけに書く。** 実装には手を入れない。

実行::

    .venv/bin/python -m benchmarks.measure_item_key_break
"""

from __future__ import annotations

import argparse
import collections
import itertools
import random

from benchmarks.measure_item_key_variance import (
    NORMALISERS,
    OKURIGANA,
    STANDARD_ITEMS,
    TRUE_VARIANTS,
    n3,
)

#: 部位の語。**品目名の先頭に現れるものだけを機械的に拾う。**
PARTS: tuple[str, ...] = ("床", "壁", "天井")

#: 動作の語。**品目名の末尾に現れるものだけを機械的に拾う。**
ACTIONS: tuple[str, ...] = ("撤去", "張り", "塗り", "敷き")


def n4(text: str) -> str:
    """N3 + 送り仮名を落とす。"""
    out = text
    for long, short in OKURIGANA:
        out = out.replace(long, short)
    return n3(out)


ALL_NORMALISERS = NORMALISERS + (("N4 送り仮名も落とす", n4),)


def materials() -> tuple[str, ...]:
    """品目名から部位と動作を除いた残りを材料として拾う。"""
    found: list[str] = []
    for name in STANDARD_ITEMS:
        body = name
        for action in ACTIONS:
            if body.endswith(action):
                body = body[: -len(action)]
                break
        for part in PARTS:
            if body.startswith(part):
                body = body[len(part):]
                break
        if body and body not in found:
            found.append(body)
    return tuple(found)


def build_keys() -> dict[str, tuple[str, str, str]]:
    """部位 × 材料 × 動作 の全組み合わせ。鍵 → (部位, 材料, 動作)。"""
    out: dict[str, tuple[str, str, str]] = {}
    for part in PARTS:
        for material in materials():
            for action in ACTIONS:
                out[f"{part}{material}{action}"] = (part, material, action)
    return out


def collisions(keys: dict[str, tuple[str, str, str]], norm) -> dict:
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for name in keys:
        buckets[norm(name)].append(name)
    b1 = b2 = b3 = 0
    for names in buckets.values():
        if len(names) < 2:
            continue
        for a, b in itertools.combinations(names, 2):
            b1 += 1
            if keys[a][2] != keys[b][2]:
                b2 += 1
            if keys[a][0] != keys[b][0]:
                b3 += 1
    total = len(keys) * (len(keys) - 1) // 2
    return {"B1": (b1, total), "B2": b2, "B3": b3}


def true_matches(norm) -> tuple[int, int]:
    matched = total = 0
    for _, make in TRUE_VARIANTS:
        for name in STANDARD_ITEMS:
            variant = make(name)
            if variant == name:
                continue
            total += 1
            if norm(variant) == norm(name):
                matched += 1
    return matched, total


def verdict_of(result: dict) -> str:
    if result["B1"][0] == 0:
        return "どちらの向きにも使える"
    if result["B2"] == 0:
        return "向きで分ける(人へ回す向きだけ)"
    return "確定させる向きには絶対に使わない"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    mats = materials()
    keys = build_keys()
    total_pairs = len(keys) * (len(keys) - 1) // 2
    print(f"部位 {len(PARTS)} / 材料 {len(mats)} / 動作 {len(ACTIONS)}")
    print(f"B0 作った鍵: {len(keys)} / 鍵の組: {total_pairs}")

    results = {}
    for label, norm in ALL_NORMALISERS:
        r = collisions(keys, norm)
        a1, a1n = true_matches(norm)
        results[label] = {**r, "A1": (a1, a1n)}
        b1, b1n = r["B1"]
        print(f"\n=== {label} ===")
        print(f"B1 偽の一致: {b1} / {b1n} = {b1 / b1n:.6f}")
        print(f"B2 動作が違うのに同じ鍵: {r['B2']}")
        print(f"B3 部位が違うのに同じ鍵: {r['B3']}")
        print(f"B4 真の一致(32周目と同じ測り方): {a1} / {a1n} = {a1 / a1n:.3f}")

    print("\n=== 対照 ===")
    rng = random.Random(args.seed)
    for label, norm in ALL_NORMALISERS:
        counts = []
        for _ in range(10):
            shuffled: dict[str, tuple[str, str, str]] = {}
            for name, meta in keys.items():
                chars = list(name)
                rng.shuffle(chars)
                shuffled["".join(chars)] = meta
            counts.append(collisions(shuffled, norm)["B1"][0])
        print(f"対照1 {label}: 鍵の文字を並べ替えたもので偽の一致 10 回: {counts}")

    print(f"対照2 N0 の B1: {results['N0 何もしない']['B1'][0]}(0 であること)")
    repeats = [collisions(keys, n3)["B1"][0] for _ in range(3)]
    print(f"対照3 反復 3 回(N3 の B1): {repeats}")

    print("\n=== まとめ ===")
    for label, _ in ALL_NORMALISERS:
        r = results[label]
        print(f"   {label}: B1 {r['B1'][0]} / B2 {r['B2']} / B3 {r['B3']}"
              f" / 真の一致 {r['A1'][0] / r['A1'][1]:.3f} → **{verdict_of(r)}**")


if __name__ == "__main__":
    main()
