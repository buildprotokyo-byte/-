"""人が数えた記号の個数(`intake/symbol_counts.py`)を数量に直す。

**`intake/` には手を入れない。** 変換をこちら側に置くのは
`estimating/from_intake.py`・`estimating/from_room_dimensions.py` と同じ理由
(向きを逆にすると循環する)。

作るのは 1 種類だけ
-------------------
| 対象名 | 単位 | 要るもの |
|---|---|---|
| ``記号数量::<記号名>`` | 箇所 / 個 / 本 | 個数 |

**入っていない欄を既定値で埋めない。** 個数が入っていなければ数量は作らず、
**なぜ作らなかったか**を `gaps` に並べる(「行が無い」で終わらせない)。

同じ記号名が 2 回入っても、自動で足さない
-----------------------------------------
図面が複数ページに分かれていると、同じ記号を別のページで数えることがある。
**足すかどうかは人が決める。** 自動で合計すると、

- 同じページを 2 回数えた(二重計上)
- 別のページを数えた(合計してよい)

の 2 つが区別できないまま合計だけが残る。どちらも同じ形で入ってくるので、
**この層では区別できない。** だから両方をそのまま出して、`gaps` に並べる。

確定はさせない
--------------
`method_id` は `arbitration/method_policies.py` に
``calibrated=False`` / 上限 ``weak`` で登録してある。
**ここを校正済みにすると、人が 1 回数えた値と図面の印字が合っただけで
階層1(自動確定)に届いてしまう。** 人が数えた個数は図面とは別のデータ源なので、
「独立した強い軸が 2 つ」の形が成立してしまうためである。

この層は `action` も `confirmed_range` も付けない。確定は仲裁層が決める。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from intake.symbol_counts import (
    COUNT_METHOD_UNKNOWN,
    METHOD_HUMAN_SYMBOL_COUNT,
    SymbolCount,
)
from estimating.quantities import QuantityItem

#: 対象名の頭。`estimating/rules.py` の規則はこの文字列に当てる。
KIND_SYMBOL_COUNT = "記号数量"

#: 数量の出どころの区分。図面から読んだものと混ぜない。
SOURCE_HUMAN_INPUT = "human"

#: どの数量にも必ず付ける注記。**図面から読んだ値ではないことを黙らせない。**
HUMAN_COUNT_NOTE = (
    "人が図面を見て数えた個数である。**図面から自動で読んだ値ではない。**"
    "数え落とし・二重数えはこの値の中では見つけられない"
)

#: ページの申告が無いときの注記。
NO_PAGE_NOTE = (
    "どのページを数えたかの申告が無い。**ほかの経路と突き合わせる相手を選べない**"
)

#: 0 と申告されたときの注記。**「入れていない」と混ぜない。**
ZERO_NOTE = (
    "0 と申告された。これは「数えたが 1 つも無かった」という主張であって、"
    "「入れていない」ではない"
)


@dataclass(frozen=True)
class SymbolQuantityResult:
    """人が数えた個数から作った数量と、**作らなかった理由**。"""

    quantities: tuple[QuantityItem, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"人が数えた個数から作った数量: {len(self.quantities)} 件 / "
            f"作らなかった理由: {len(self.gaps)} 件"
        )


def quantities_from_symbol_counts(
    counts: Sequence[SymbolCount],
) -> SymbolQuantityResult:
    """記号の個数の並びから数量を作る。**空の入力からは何も作らない。**"""
    quantities: list[QuantityItem] = []
    gaps: list[str] = []

    names = Counter(entry.symbol_name.strip() for entry in counts)

    for entry in counts:
        name = entry.symbol_name.strip()

        if not entry.has_count:
            gaps.append(
                f"[入力が足りない] {name}: 個数が入っていないので数量を作っていない。"
                "**0 で埋めない**"
            )
            continue

        notes: tuple[str, ...] = (HUMAN_COUNT_NOTE,)
        if entry.page_number is None:
            notes += (NO_PAGE_NOTE,)
        if entry.count == 0:
            notes += (ZERO_NOTE,)

        attributes: dict[str, str] = {"記号名": name}
        # **読めなかった属性は入れない。** 空文字で埋めると、
        # 「申告が無い」と「空と申告された」が区別できなくなる。
        if entry.page_number is not None:
            attributes["ページ"] = str(entry.page_number)
        if entry.counted_with != COUNT_METHOD_UNKNOWN:
            attributes["数え方"] = entry.counted_with
        if entry.entered_by:
            attributes["入れた人"] = entry.entered_by

        quantities.append(
            QuantityItem(
                target=f"{KIND_SYMBOL_COUNT}::{name}",
                value_range=(float(entry.count), float(entry.count)),
                unit=entry.unit,
                method_id=METHOD_HUMAN_SYMBOL_COUNT,
                source_kind=SOURCE_HUMAN_INPUT,
                axis_id="human",
                derivation="read",
                attributes=attributes,
                provenance=_provenance(entry),
                notes=notes,
            )
        )

    for name, times in sorted(names.items()):
        if times > 1:
            gaps.append(
                f"[同じ記号名が{times}回] {name}: **自動で足していない。**"
                "同じページを2回数えたのか、別のページを数えたのかが"
                "この層では区別できないため、合計してよいかは人が決める"
            )

    return SymbolQuantityResult(quantities=tuple(quantities), gaps=tuple(gaps))


def _provenance(entry: SymbolCount) -> dict[str, object]:
    """**図面とは別のデータ源であることを、根拠に必ず残す。**"""
    return {
        "method_id": METHOD_HUMAN_SYMBOL_COUNT,
        "entered_by": entry.entered_by,
        "counted_with": entry.counted_with,
        "page_number": entry.page_number,
        "unit": entry.unit,
        "independence_note": (
            "人が数えた個数は、図面の線や印字とは**別のデータ源**である。"
            "図面から読んだ値と一致しても、それは独立した 2 つの証言ではあるが、"
            "**どちらも校正されていない。** この手法を校正済みにすると、"
            "人が 1 回入れた値と印字の一致だけで自動確定に届く"
        ),
    }


__all__ = [
    "HUMAN_COUNT_NOTE",
    "KIND_SYMBOL_COUNT",
    "NO_PAGE_NOTE",
    "SOURCE_HUMAN_INPUT",
    "SymbolQuantityResult",
    "ZERO_NOTE",
    "quantities_from_symbol_counts",
]
