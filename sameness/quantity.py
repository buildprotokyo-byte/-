"""K-66 1 節: 数量は名前と**別に**判定する。許容差は個数 ±1、面積・長さ ±5%、`式` は判定しない。

同じ値は `benchmarks/k54_public_format_score.py` の `close()` が既に使っている
(K-49 でおーちゃんが決めた許容差)。**2 か所に別の数字を置かない**ので、ここが正本になり、
あちらはここを呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sameness.normalize import canonical_unit

#: 個数の単位(±1 で判定する)。
COUNT_UNITS = ("箇所", "個", "台", "枚", "本", "組", "灯", "面", "回路", "人")

#: 連続量の単位(±5% で判定する)。
CONTINUOUS_UNITS = ("m", "m2", "m3", "mm", "cm", "kg", "t")

#: 判定しない単位。**`式` は数量の意味が案件ごとに違う**(K-36 で分母から外した 6 行がこれ)。
NO_JUDGE_UNITS = ("式", "一式", "回", "日", "")

RATIO = 0.05

MATCH = "合う"
DIFFER = "違う"
NOT_JUDGED = "判定しない"
MISSING = "比較不能"


@dataclass(frozen=True)
class QuantityVerdict:
    value: str
    reason: str
    unit: str

    @property
    def hit(self) -> bool:
        return self.value == MATCH


def tolerance(unit: Any, reference: float | None) -> float | None:
    """許容差(絶対値)。判定しない単位は `None`。"""
    canonical = canonical_unit(unit)
    if canonical in NO_JUDGE_UNITS:
        return None
    if canonical in COUNT_UNITS:
        return 1.0
    return RATIO * abs(reference) if reference else 0.0


def close(a: float | None, b: float | None, unit: Any) -> bool:
    """`b` を基準にして `a` が許容差の中か。**判定しない単位は False**(当たりにしない)。"""
    if a is None or b is None:
        return False
    limit = tolerance(unit, b)
    if limit is None:
        return False
    return abs(a - b) <= limit


def quantity_verdict(a: Any, b: Any, unit: Any, *, unit_b: Any = None) -> QuantityVerdict:
    """数量 2 つの判定。**単位が違えば数の比較をしない。**"""
    canonical = canonical_unit(unit)
    other = canonical_unit(unit_b) if unit_b is not None else canonical
    if other and canonical and other != canonical:
        return QuantityVerdict(DIFFER, f"単位が {canonical} と {other} で違う", canonical)
    if canonical in NO_JUDGE_UNITS:
        return QuantityVerdict(NOT_JUDGED, f"単位 {canonical or '(空)'} は判定しない", canonical)
    try:
        left = None if a is None or a == "" else float(a)
        right = None if b is None or b == "" else float(b)
    except (TypeError, ValueError):
        return QuantityVerdict(MISSING, "数量が数に読めない", canonical)
    if left is None or right is None:
        return QuantityVerdict(MISSING, "数量が片方(または両方)未取得", canonical)
    limit = tolerance(canonical, right)
    if limit is not None and abs(left - right) <= limit:
        return QuantityVerdict(MATCH, f"差 {abs(left-right):.4g} は許容差 {limit:.4g} の中", canonical)
    return QuantityVerdict(DIFFER, f"差 {abs(left-right):.4g} が許容差 {limit:.4g} を超える", canonical)
