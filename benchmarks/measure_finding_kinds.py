"""23周目の測定: 指摘を桁違いだけにしたら、空振りが消えて検出力が落ちないか。

**出すのは件数と割合だけ。** 寸法の値そのもの・室名は印字しない。

実行::

    .venv/bin/python -m benchmarks.measure_finding_kinds --pdf <図面.pdf>
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
from benchmarks.measure_printed_dimension_check import PLAN_PAGE, RANDOM_TRIES
from estimating.dimension_check import check_values


def rates(values: list[int], printed: tuple[int, ...]) -> tuple[float, float]:
    """(指摘として出た割合, 確かめられない率)。"""
    result = check_values(
        "測定", [(f"値{i}", float(v)) for i, v in enumerate(values)], printed
    )
    total = len(result.findings) or 1
    return len(result.mismatches) / total, (result.unverifiable_rate or 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()

    printed = distinct_values(read_printed_dimensions(args.pdf, PLAN_PAGE))
    print(f"{PLAN_PAGE} ページの印字: {len(printed)} 通り")

    pairs = sorted({a + b for a in printed for b in printed})
    v1, v4_pairs = rates(pairs, printed)
    print(f"\nV1 正しい見込みの値(2 区間の和 {len(pairs)} 通り)が**指摘として出た**割合: {v1:.3f}")
    print(f"   (22 周目の出し方では 0.986 が「食い違い」として出ていた)")

    shifted = [v * 10 for v in printed] + [v // 10 for v in printed]
    v2, _ = rates(shifted, printed)
    print(f"\nV2 桁違い({len(shifted)} 通り)を指摘として出した割合: {v2:.3f}")

    rng = random.Random(args.seed)
    randoms = [rng.randint(MIN_DIMENSION_MM, MAX_DIMENSION_MM) for _ in range(RANDOM_TRIES)]
    v3, v4_random = rates(randoms, printed)
    print(f"\nV3 でたらめな寸法 {RANDOM_TRIES} 個が指摘として出た割合: {v3:.3f}")

    print(f"\nV4 確かめられない率")
    print(f"   正しい見込みの値: {v4_pairs:.3f}")
    print(f"   でたらめな寸法  : {v4_random:.3f}")

    print(f"\n=== 対照 ===")
    repeats = [round(rates(pairs, printed)[0], 4) for _ in range(3)]
    print(f"  対照1 反復 3 回の V1: {repeats}")
    seeds = []
    for seed in (1, 2, 3):
        rng2 = random.Random(seed)
        values = [rng2.randint(MIN_DIMENSION_MM, MAX_DIMENSION_MM) for _ in range(RANDOM_TRIES)]
        seeds.append(round(rates(values, printed)[0], 4))
    print(f"  対照2 乱数の種を変えて 3 回の V3: {seeds}")
    empty = check_values("測定", [("縦", 3640.0)], [])
    print(
        f"  対照3 印字が空: 指摘 {len(empty.mismatches)} 件 / "
        f"確かめられない率 {empty.unverifiable_rate:.3f}"
    )


if __name__ == "__main__":
    main()
