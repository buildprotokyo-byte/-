"""人が入れた室の寸法(`intake/room_dimensions.py`)を数量に直す。

**`intake/` には手を入れない。** 変換をこちら側に置くのは
`estimating/from_intake.py` と同じ理由(向きを逆にすると循環する)。

作るのは 3 種類だけ
-------------------
| 対象名 | 単位 | 要るもの |
|---|---|---|
| ``床面積::<室名>`` | ㎡ | 縦・横 |
| ``室の周長::<室名>`` | m | 縦・横 |
| ``内壁面積::<室名>`` | ㎡ | 縦・横・天井高 |

**入っていない欄を既定値で埋めない。** 天井高が無ければ内壁面積は作らず、
**なぜ作らなかったか**を `gaps` に並べる(「行が無い」で終わらせない)。

確定はさせない
--------------
`method_id` は `arbitration/method_policies.py` に
``calibrated=False`` / 上限 ``weak`` で登録してある。
**ここを校正済みにすると、人が 1 回入れた値と図面の印字が合っただけで
階層1(自動確定)に届いてしまう。** 人の入力は図面とは別のデータ源なので、
「独立した強い軸が 2 つ」の形が成立してしまうためである。

この層は `action` も `confirmed_range` も付けない。確定は仲裁層が決める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from intake.room_dimensions import (
    BASIS_UNKNOWN,
    METHOD_HUMAN_ROOM_DIMENSIONS,
    RoomDimension,
)
from estimating.quantities import QuantityItem

#: 対象名の頭。`estimating/rules.py` の規則はこの文字列に当てる。
KIND_FLOOR_AREA = "床面積"
KIND_PERIMETER = "室の周長"
KIND_WALL_AREA = "内壁面積"

#: 数量の出どころの区分。図面から読んだものと混ぜない。
SOURCE_HUMAN_INPUT = "human"

#: 壁の面積に必ず付ける注記。**開口を引いていないことを黙らせない。**
WALL_OPENING_NOTE = (
    "建具・開口の面積を引いていない(この入力に建具の大きさが入っていないため)。"
    "**引いた値が要るなら、建具の数量と突き合わせてから人が決める。**"
)

#: 長方形とみなしていることの注記。
RECTANGLE_NOTE = (
    "室を長方形とみなして計算した。L 字・凹みのある室では周長が実際より短く出る"
)


@dataclass(frozen=True)
class RoomQuantityResult:
    """人が入れた寸法から作った数量と、**作らなかった理由**。"""

    quantities: tuple[QuantityItem, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"人が入れた寸法から作った数量: {len(self.quantities)} 件 / "
            f"作らなかった理由: {len(self.gaps)} 件"
        )


def quantities_from_room_dimensions(
    dimensions: Sequence[RoomDimension],
) -> RoomQuantityResult:
    """室の寸法の並びから数量を作る。**空の入力からは何も作らない。**"""
    quantities: list[QuantityItem] = []
    gaps: list[str] = []
    seen: set[str] = set()

    for dimension in dimensions:
        name = dimension.room_name.strip()
        if name in seen:
            # 同じ室名が 2 回入ると、下流ではどちらの寸法が使われたか分からない。
            # 片方を選ぶと、選ばなかったほうが黙って消える。
            gaps.append(
                f"[同じ室名が2回] {name}: どちらの寸法を使うか決められないので"
                "どちらも数量にしていない"
            )
            continue
        seen.add(name)

        if not dimension.has_plan:
            gaps.append(
                f"[入力が足りない] {name}: {'・'.join(dimension.missing())}が"
                "入っていないので、床面積も周長も出していない"
            )
            continue

        provenance = _provenance(dimension)
        notes_common = (RECTANGLE_NOTE,)
        if dimension.area_basis == BASIS_UNKNOWN:
            notes_common += (
                "寸法をどこで測ったか(芯々か内法か)が申告されていない。"
                "**芯々として扱っていない。**",
            )
            gaps.append(
                f"[測り方が不明] {name}: 芯々か内法かの申告が無い。"
                "面積は芯々で数えると決まっているので、内法で入れた数字なら小さく出る"
            )

        floor = dimension.floor_area_cm2_range()
        quantities.append(
            QuantityItem(
                target=f"{KIND_FLOOR_AREA}::{name}",
                value_range=_cm2_to_sqm(floor),
                unit="㎡",
                method_id=METHOD_HUMAN_ROOM_DIMENSIONS,
                source_kind=SOURCE_HUMAN_INPUT,
                axis_id="human",
                derivation="derived",
                derivation_basis=("read",),
                provenance=provenance,
                notes=notes_common,
            )
        )
        quantities.append(
            QuantityItem(
                target=f"{KIND_PERIMETER}::{name}",
                value_range=_mm_to_m(dimension.perimeter_mm),
                unit="m",
                method_id=METHOD_HUMAN_ROOM_DIMENSIONS,
                source_kind=SOURCE_HUMAN_INPUT,
                axis_id="human",
                derivation="derived",
                derivation_basis=("read",),
                provenance=provenance,
                notes=notes_common,
            )
        )

        if not dimension.has_walls:
            gaps.append(
                f"[入力が足りない] {name}: 天井高が入っていないので内壁の面積を"
                "出していない。**既定の天井高で埋めない**"
            )
            continue

        wall = dimension.wall_area_cm2_range()
        quantities.append(
            QuantityItem(
                target=f"{KIND_WALL_AREA}::{name}",
                value_range=_cm2_to_sqm(wall),
                unit="㎡",
                method_id=METHOD_HUMAN_ROOM_DIMENSIONS,
                source_kind=SOURCE_HUMAN_INPUT,
                axis_id="human",
                derivation="derived",
                derivation_basis=("read",),
                provenance=provenance,
                notes=notes_common + (WALL_OPENING_NOTE,),
            )
        )

    return RoomQuantityResult(quantities=tuple(quantities), gaps=tuple(gaps))


def _provenance(dimension: RoomDimension) -> dict[str, object]:
    """**独立でないことを、根拠に必ず残す。**"""
    return {
        "method_id": METHOD_HUMAN_ROOM_DIMENSIONS,
        "entered_by": dimension.entered_by,
        "area_basis": dimension.area_basis,
        "length_mm": dimension.length_mm,
        "width_mm": dimension.width_mm,
        "ceiling_height_mm": dimension.ceiling_height_mm,
        "independence_note": (
            "床面積・周長・内壁面積は、同じ人が同じ時に入れた縦・横・天井高から"
            "計算したものである。**3 つの独立した証言ではない。**"
            "入力の取り違えは 3 つ全部に同じように効くので、"
            "互いを突き合わせても誤りは見つからない。"
        ),
    }


def _cm2_to_sqm(cm2_range: tuple[int, int]) -> tuple[float, float]:
    """1cm² きざみの整数範囲を ㎡ に直す。**10 進で計算する。**"""
    return (
        float(Decimal(cm2_range[0]) / Decimal(10_000)),
        float(Decimal(cm2_range[1]) / Decimal(10_000)),
    )


def _mm_to_m(value_mm: float) -> tuple[float, float]:
    """mm を m に直す。周長は mm きざみでちょうど表せるので範囲にしない。"""
    metres = float(Decimal(str(value_mm)) / Decimal(1_000))
    return (metres, metres)


__all__ = [
    "KIND_FLOOR_AREA",
    "KIND_PERIMETER",
    "KIND_WALL_AREA",
    "RECTANGLE_NOTE",
    "RoomQuantityResult",
    "SOURCE_HUMAN_INPUT",
    "WALL_OPENING_NOTE",
    "quantities_from_room_dimensions",
]
