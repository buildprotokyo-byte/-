"""人が入れた室の寸法を、図面に印字された寸法と突き合わせる。

2026-09-23、22周目。**この層が出すのは「食い違っている」という指摘だけである。**

一致しても確定させない(いちばん大事なところ)
----------------------------------------------
人が入れた寸法(`intake/room_dimensions.py`)と、図面の印字
(`axes/image_axis/printed_dimensions.py`)は、**別のデータ源**である。

**だからこそ、一致を根拠に確定させてはいけない。**
`intake/start_kit.py` の基準点と同じ構図で、
**人が 1 回入れた値と印字が合っただけで、独立した強い軸が 2 つ揃ってしまう。**
両方とも未校正のままにする。

この層は `action` も `confirmed_range` も `tier` も付けない。**数量も作らない。**

**足し合わせない**(測って決めた)
---------------------------------
壁の寸法は区間ごとに分けて印字されるので、「印字された値の和」と突き合わせたくなる。
**しかし和を許すと、どんな数でも一致してしまう。**
22 周目に測ったとおりで、詳しくは `docs/a2_printed_dimension_report.md` にある。

**だから、ここが見るのは「その値がそのまま印字されているか」だけである。**
印字が区間に分かれている室は「印字に無い」として出す。
**見つけられないものを見つけたことにしない。**

桁違いだけは名指しする
----------------------
`3640` と `364`、`364` と `36400` のような桁違いは、
**人の入力でいちばん起きやすい誤り**である(9 周目のモジュールにも書いた)。
そのまま印字に無くても、10 倍・10 分の 1 が印字にあれば、**そう名指しして出す。**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from axes.image_axis.printed_dimensions import METHOD_PRINTED_DIMENSION

#: 突き合わせの結果。
FOUND = "印字にある"
NOT_FOUND = "印字に無い"
DIGIT_SHIFT = "桁が違う見込み"

#: 桁違いとして見る倍率。
DIGIT_FACTORS: tuple[int, ...] = (10, 100)


@dataclass(frozen=True)
class DimensionFinding:
    """人が入れた値 1 つについての突き合わせの結果。"""

    room_name: str
    label: str
    """`縦` `横` `天井高` のどれか。"""

    value_mm: float
    status: str
    note: str = ""

    @property
    def is_mismatch(self) -> bool:
        return self.status != FOUND


@dataclass(frozen=True)
class DimensionCheckResult:
    findings: tuple[DimensionFinding, ...] = ()
    printed_count: int = 0
    """突き合わせに使った印字の値の数(重複を除いたもの)。"""

    method_id: str = METHOD_PRINTED_DIMENSION

    @property
    def mismatches(self) -> tuple[DimensionFinding, ...]:
        return tuple(f for f in self.findings if f.is_mismatch)

    def summary(self) -> str:
        return (
            f"突き合わせ {len(self.findings)} 件 / "
            f"食い違い {len(self.mismatches)} 件 / "
            f"印字の値 {self.printed_count} 通り"
        )


def check_values(
    room_name: str,
    values: Sequence[tuple[str, float | None]],
    printed_values: Iterable[int],
) -> DimensionCheckResult:
    """人が入れた値を、印字された値の集合と突き合わせる。

    `values` は `[("縦", 3640.0), ("横", None), ...]` の形。
    **`None` は突き合わせない**(入れていないものを誤りにしない)。
    """
    printed = set(int(v) for v in printed_values)
    findings: list[DimensionFinding] = []

    for label, value in values:
        if value is None:
            continue
        rounded = int(round(value))
        if rounded in printed:
            findings.append(
                DimensionFinding(room_name, label, value, FOUND)
            )
            continue

        shifted = _digit_shift(rounded, printed)
        if shifted is not None:
            findings.append(
                DimensionFinding(
                    room_name,
                    label,
                    value,
                    DIGIT_SHIFT,
                    f"入った値の {shifted} 倍か {shifted} 分の 1 が図面に印字されている。"
                    "**桁の取り違えを疑ってください**",
                )
            )
            continue

        findings.append(
            DimensionFinding(
                room_name,
                label,
                value,
                NOT_FOUND,
                "この値はこのページに印字されていない。"
                "**間違いとは限らない**(壁の寸法が区間に分かれて印字されていると、"
                "合計はどこにも印字されない)",
            )
        )

    return DimensionCheckResult(tuple(findings), len(printed))


def _digit_shift(value: int, printed: set[int]) -> int | None:
    for factor in DIGIT_FACTORS:
        if value * factor in printed:
            return factor
        if value % factor == 0 and value // factor in printed:
            return factor
    return None


__all__ = [
    "DIGIT_FACTORS",
    "DIGIT_SHIFT",
    "DimensionCheckResult",
    "DimensionFinding",
    "FOUND",
    "NOT_FOUND",
    "check_values",
]
