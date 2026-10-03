"""K-66 1 節: 数量は名前と**別に**判定する。許容差は個数 ±1、面積・長さ ±5%、`式` は判定しない。

K-71 作業1 b: **新の線(行の性質ごと)と単位の同値もここだけに置く。**旧の線は `side="旧"`(既定)で
今までどおり。新は `side="新", nature=1〜6`。`quantity_verdicts()` が旧と新を並べて返す。
基準は `docs/k71_scoring_lines_criteria.md` 1 節。

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


def quantity_verdict(a: Any, b: Any, unit: Any, *, unit_b: Any = None, side: str = "旧",
                     nature: int | None = None) -> QuantityVerdict:
    """数量 2 つの判定。**単位が違えば数の比較をしない。**

    `side="旧"`(既定)= K-66 の線(±1 / ±5%、単位の同値なし)。`side="新"` = K-71 の性質ごとの線。
    """
    if side == "新":
        return _new_verdict(a, b, unit, unit_b, nature)
    if side != "旧":
        raise ValueError(f"side は '旧' か '新': {side!r}")
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


# --------------------------------------------------------------------------- K-71 新の線

#: K-71 おーちゃん: 同じ単位とみなす個数の単位(新の側だけ)。**式・本・組・セットは入れない。**
#: `ヶ所`・`カ所`・`ケ所` は `canonical_unit` で既に `箇所` に揃う。
NEW_COUNT_EQUIVALENT = ("個", "箇所", "台", "枚")
NEW_COUNT_LABEL = "個(個・箇所・台・枚)"

#: 性質ごとの線(K-71 作業1。固定)。
NEW_LINES: dict[int, dict[str, float]] = {
    1: {"合う": 0.10, "最小": 1.0},   # 図面から数える個数: ±10%、少ないときは ±1
    2: {"合う": 0.10, "近い": 0.30},  # 図面から測る面積・長さ: ±10%(±30% は「近い」)
    3: {"合う": 0.15},                # 仕上面積から出る派生: ±15%
}
NATURES = (1, 2, 3, 4, 5, 6)
NEAR = "近い"
BY_REASON = "理由で見る"
PRESENCE_ONLY = "有無だけ"

#: 長さ・面積・体積の単位(新の側で性質を単位から決めるとき)。
NEW_CONTINUOUS = ("m", "m2", "m3", "mm", "cm")
#: 個数の単位(新の側で性質を単位から決めるとき。同値でなくても個数ではある)。
NEW_COUNTS = ("個", "箇所", "台", "枚", "本", "組", "灯", "面", "回路")


def new_unit(value: Any) -> str:
    """新の側の単位の正規形。**個・箇所・ヶ所・カ所・ケ所・台・枚 を 1 つにする。**ほかは旧と同じ。"""
    canonical = canonical_unit(value)
    return NEW_COUNT_LABEL if canonical in NEW_COUNT_EQUIVALENT else canonical


def nature_from_unit(unit: Any) -> int | None:
    """性質が渡されなかったときに単位から決める: 個数 → 1、長さ・面積・体積 → 2、それ以外 → None。"""
    canonical = canonical_unit(unit)
    if canonical in NEW_COUNTS:
        return 1
    if canonical in NEW_CONTINUOUS:
        return 2
    return None


def new_tolerance(nature: int, reference: float) -> dict[str, float]:
    """新の線の許容(絶対値)。`{"合う": x}`、性質 2 は `近い` も。"""
    line = NEW_LINES[nature]
    out = {"合う": line["合う"] * abs(reference)}
    if "最小" in line:
        out["合う"] = max(out["合う"], line["最小"])
    if "近い" in line:
        out["近い"] = line["近い"] * abs(reference)
    return out


def _new_verdict(a: Any, b: Any, unit: Any, unit_b: Any, nature: int | None) -> QuantityVerdict:
    left_unit = new_unit(unit)
    right_unit = new_unit(unit_b) if unit_b is not None else left_unit
    if nature in (4, 5):
        return QuantityVerdict(BY_REASON, f"性質 {nature} は数量を合否に入れない(理由で見る)", left_unit)
    if nature == 6:
        return QuantityVerdict(PRESENCE_ONLY, "性質 6(波及)は有無だけ。数量は参考", left_unit)
    if right_unit and left_unit and right_unit != left_unit:
        return QuantityVerdict(DIFFER, f"単位が {left_unit} と {right_unit} で違う", left_unit)
    if nature is None:
        nature = nature_from_unit(right_unit or left_unit)
        if nature is None:
            return QuantityVerdict(NOT_JUDGED, f"単位 {left_unit or '(空)'} は判定しない", left_unit)
    if nature not in NEW_LINES:
        raise ValueError(f"性質は 1〜6: {nature!r}")
    try:
        left = None if a is None or a == "" else float(a)
        right = None if b is None or b == "" else float(b)
    except (TypeError, ValueError):
        return QuantityVerdict(MISSING, "数量が数に読めない", left_unit)
    if left is None or right is None:
        return QuantityVerdict(MISSING, "数量が片方(または両方)未取得", left_unit)
    limits = new_tolerance(nature, right)
    diff = abs(left - right)
    eps = 1e-9  # 0.1×3 のような小数の誤差で境目(以下で合う)を外さない
    if diff <= limits["合う"] + eps:
        return QuantityVerdict(MATCH, f"性質 {nature}: 差 {diff:.4g} は許容 {limits['合う']:.4g} の中", left_unit)
    if "近い" in limits and diff <= limits["近い"] + eps:
        return QuantityVerdict(NEAR, f"性質 {nature}: 差 {diff:.4g} は ±30% {limits['近い']:.4g} の中", left_unit)
    return QuantityVerdict(DIFFER, f"性質 {nature}: 差 {diff:.4g} が許容 {limits['合う']:.4g} を超える", left_unit)


def quantity_verdicts(a: Any, b: Any, unit: Any, *, unit_b: Any = None,
                      nature: int | None = None) -> dict[str, QuantityVerdict]:
    """旧と新を並べて返す(K-71 作業1 b)。"""
    return {"旧": quantity_verdict(a, b, unit, unit_b=unit_b),
            "新": quantity_verdict(a, b, unit, unit_b=unit_b, side="新", nature=nature)}
