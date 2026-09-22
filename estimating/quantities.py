"""当てはめの入力になる「数量 1 件」。

入口(`intake/drawing_intake.py`)の `DrawingFinding` をそのまま使わずに
中立の形を置くのは、数量の出どころが図面だけではないからである。
人が入れた前提(スタートキット)も、将来つなぐ別のデータ源も、同じ形に
そろえてからこの層に入れる。**`intake/` には手を入れない。**
変換は `estimating/from_intake.py` 側が持つ。

ここが守ること
--------------
1. **単位は作った時点で検査する。** 解釈できない単位の数量は作らせない。
   下流で黙って落ちるより、入口で止めたほうが原因が分かる。
2. **確定しているかどうかは、この層が決め直さない。** 仲裁層が出した
   `action` と `confirmed_range` をそのまま運ぶ。`is_confirmed` は
   その2つが揃ったときだけ真になる。
3. **読めなかった属性は入れない。** 空文字や既定値で埋めない。属性が
   無いことと、属性が空であることは別である。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from arbitration.units import UnitError, canonical_unit, normalise_range

#: 対象名の中で「種類」と「個別の鍵」を分ける印。
#: 入口が `建具数量::AW-1` `開き戸::ページ1` の形で出している。
TARGET_SEPARATOR = "::"


class QuantityError(Exception):
    """数量として受け付けられなかった。"""


def split_target(target: str) -> tuple[str, str | None]:
    """対象名を(種類, 鍵)に分ける。

    規則は種類のほうに当てる。`建具数量::AW-1` の全文に当てると、建具番号が
    1 つ増えるたびに規則を書き足すことになる。
    """
    if TARGET_SEPARATOR in target:
        kind, key = target.split(TARGET_SEPARATOR, 1)
        return kind, key
    return target, None


def normalise_text(value: str) -> str:
    """属性の突き合わせに使う正規化。

    全角と半角の違い(`ＳＤ` と `SD`)だけで規則が当たらなくなるのを防ぐ。
    **表記をそろえるだけで、意味は変えない。** 元の文字列は
    `QuantityItem.attributes` に入っているものがそのまま残る。
    """
    return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True)
class QuantityItem:
    """当てはめの対象になる数量 1 件。"""

    target: str
    """入口が付けた対象名。追跡のためそのまま持つ。"""

    value_range: tuple[float, float]
    """読めた値。1 点で読めたなら下限=上限。**宣言した単位のまま。**"""

    unit: str
    """印字どおりの表記(`箇所` `㎡`)。`arbitration/units.py` が解釈する。"""

    method_id: str
    source_kind: str = "drawing"
    axis_id: str = "image"

    tier: int | None = None
    """仲裁層が出した階層。まだ仲裁にかけていなければ None。"""

    action: str | None = None
    """`auto_confirm` / `requires_review` など。"""

    confirmed_range: tuple[int, int] | None = None
    """**正規形単位の整数**。確定していなければ None。"""

    attributes: Mapping[str, str] = field(default_factory=dict)
    """規則が条件に使える属性(建具表の種別など)。**読めたものだけ。**"""

    provenance: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    """この数量について記録しておくこと(属性が食い違った、など)。"""

    def __post_init__(self) -> None:
        if not self.target:
            raise QuantityError("target は空にできません")
        try:
            canonical_unit(self.unit)
            normalise_range(self.unit, self.value_range[0], self.value_range[1])
        except UnitError as error:
            raise QuantityError(
                f"{self.target} の単位または値が受け付けられません: {error}"
            ) from error
        for name, value in self.attributes.items():
            if not isinstance(value, str) or not value.strip():
                raise QuantityError(
                    f"{self.target} の属性 {name} が文字列ではありません"
                    "(読めなかった属性は入れないでください)"
                )

    @property
    def kind(self) -> str:
        return split_target(self.target)[0]

    @property
    def key(self) -> str | None:
        return split_target(self.target)[1]

    @property
    def canonical_unit(self) -> str:
        return canonical_unit(self.unit)

    @property
    def canonical_range(self) -> tuple[int, int]:
        """読めた値を正規形の整数に直したもの。"""
        return normalise_range(self.unit, self.value_range[0], self.value_range[1])[1]

    @property
    def is_confirmed(self) -> bool:
        """**仲裁層が自動確定したか。** この層は判定をやり直さない。"""
        return self.action == "auto_confirm" and self.confirmed_range is not None

    def attribute(self, name: str) -> str | None:
        return self.attributes.get(name)
