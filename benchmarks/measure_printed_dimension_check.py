"""22周目の測定: 印字された寸法との突き合わせが、検算として成り立つか。

**いちばん大事なのは「でたらめな寸法でも合ってしまう割合」である。**
何にでも一致する検算は、検算ではない。

**出すのは件数と割合だけ。** 寸法の値そのもの・室名は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_printed_dimension_check --pdf <図面.pdf>
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from axes.image_axis.printed_dimensions import (
    MAX_DIMENSION_MM,
    MIN_DIMENSION_MM,
    distinct_values,
    read_printed_dimensions,
)
from estimating.dimension_check import DIGIT_SHIFT, FOUND, check_values

#: 測るページ(1 始まり)。8 ページは改装平面図(既存の参照データと同じページ)。
PLAN_PAGE = 8

#: 対照1 に使う、図面でないページ(1 始まり)。
NON_DRAWING_PAGES = (1, 26)

#: でたらめな寸法をいくつ試すか。
RANDOM_TRIES = 1000


def agreement_rate(values: list[int], printed: tuple[int, ...]) -> tuple[float, float]:
    """(そのまま一致した割合, 桁違いと言われた割合)。"""
    result = check_values(
        "測定", [(f"値{i}", float(v)) for i, v in enumerate(values)], printed
    )
    found = sum(1 for f in result.findings if f.status == FOUND)
    shifted = sum(1 for f in result.findings if f.status == DIGIT_SHIFT)
    total = len(result.findings) or 1
    return found / total, shifted / total


def sum_agreement_rate(values: list[int], printed: tuple[int, ...]) -> float:
    """**もし足し合わせを許したら**どれだけ一致してしまうか(参考。実装はしない)。"""
    pairs = {a + b for a in printed for b in printed}
    triples = {a + b + c for a in printed for b in printed for c in printed}
    allowed = set(printed) | pairs | triples
    return sum(1 for v in values if v in allowed) / (len(values) or 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    printed = distinct_values(read_printed_dimensions(args.pdf, PLAN_PAGE))
    print(f"=== U0 拾えた寸法の候補 ===")
    print(f"  {PLAN_PAGE} ページ: 異なる値 {len(printed)} 通り")
    for page in NON_DRAWING_PAGES:
        other = distinct_values(read_printed_dimensions(args.pdf, page))
        print(f"  対照1 図面でない {page:2d} ページ: 異なる値 {len(other)} 通り")

    rng = random.Random(args.seed)
    randoms = [rng.randint(MIN_DIMENSION_MM, MAX_DIMENSION_MM) for _ in range(RANDOM_TRIES)]
    found, shifted = agreement_rate(randoms, printed)
    print(f"\n=== U1 でたらめな寸法 {RANDOM_TRIES} 個 ===")
    print(f"  そのまま一致してしまった割合: {found:.3f}")
    print(f"  桁違いと言われた割合        : {shifted:.3f}")
    print(f"  **もし足し合わせを許したら**: {sum_agreement_rate(randoms, printed):.3f}")

    print(f"\n=== U2 印字されている値をそのまま入れる ===")
    back, _ = agreement_rate(list(printed), printed)
    print(f"  一致した割合: {back:.3f} / {len(printed)} 通り")

    print(f"\n=== U3 わざと誤らせた入力を見つけられるか ===")
    kinds = {
        "桁違い(10 倍)": [v * 10 for v in printed],
        "1 桁落ち(10 分の 1)": [v // 10 for v in printed],
        "縦横の入れ替え": list(reversed(printed)),
    }
    caught: dict[str, float] = {}
    for label, corrupted in kinds.items():
        result = check_values(
            "測定", [(f"値{i}", float(v)) for i, v in enumerate(corrupted)], printed
        )
        detected = sum(1 for f in result.findings if f.is_mismatch)
        caught[label] = detected / (len(result.findings) or 1)
        print(f"  {label:22}: 見つけた {detected:3d} / {len(result.findings):3d} = {caught[label]:.3f}")
    print(f"  U3(3 通りの平均): {sum(caught.values()) / len(caught):.3f}")

    print(f"\n=== 対照 ===")
    repeats = [
        distinct_values(read_printed_dimensions(args.pdf, PLAN_PAGE)) == printed
        for _ in range(3)
    ]
    print(f"  対照2 反復 3 回: {repeats}")
    seeds = []
    for seed in (1, 2, 3):
        rng2 = random.Random(seed)
        values = [rng2.randint(MIN_DIMENSION_MM, MAX_DIMENSION_MM) for _ in range(RANDOM_TRIES)]
        seeds.append(round(agreement_rate(values, printed)[0], 4))
    print(f"  対照3 乱数の種を変えて 3 回の U1: {seeds}")


if __name__ == "__main__":
    main()
