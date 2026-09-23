"""図面に印字された寸法の数値を拾う。

2026-09-23、22周目。**拾うだけで、何も確定させない。**
突き合わせは `estimating/dimension_check.py` が持つ。

なぜ裸の整数を寸法とみなせるのか
--------------------------------
建築工事設計図書作成基準 3.5(1) は **寸法の単位をミリメートルに固定し、
単位記号を省略する**と定めている(`docs/knowledge/candidates.md` の F 節)。
だから図面の上の裸の整数は、mm の寸法である見込みが高い。

**「見込みが高い」であって「そうである」ではない。**
図面番号・縮尺の分母・部屋番号・注記の番号も裸の整数で出てくる。
**だからこの層は「寸法の候補」としか言わない。**

ゆるくしすぎない
----------------
**何にでも一致する検算は、検算ではない。**
候補を広く取るほど、人がどんな数を入れても「印字にある」と言えてしまう。
そこで受け付ける範囲を狭くする。

- `MIN_DIMENSION_MM` 未満・`MAX_DIMENSION_MM` 超は捨てる。
- **先頭が 0 の数は捨てる**(`01` のような番号付け)。
- 小数点を含む数は捨てる(寸法は mm の整数で書かれる)。

**「桁数が 2 桁以下なら捨てる」という検査は置かない。**
22 周目の壊し試験で、この検査を外しても試験が 1 つも落ちなかった。
調べると、**窓の下限(300mm)がすでに同じことをしている**(2 桁の数は必ず 300 未満)ので、
**動きの変わらない死んだ分岐だった。** 置いておくと
「桁数でも守っている」と読めてしまうので消した。

**この層は確定させない。** 手法IDは未校正・上限 weak で登録する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: 印字された寸法の手法ID。
METHOD_PRINTED_DIMENSION = "pdf_printed_dimension"

#: 寸法として受け付ける範囲(mm)。`intake/room_dimensions.py` と同じ窓。
MIN_DIMENSION_MM = 300
MAX_DIMENSION_MM = 50_000

#: 数字の並び。**小数点を含むものは、この形に当たらない。**
_INTEGER = re.compile(r"(?<![\d.])(\d+)(?![\d.])")


@dataclass(frozen=True)
class PrintedDimension:
    """図面の上に印字されていた、寸法の候補 1 件。"""

    value_mm: int
    page_number: int
    """1 始まり。**0 始まりと取り違えないため、この層では 1 始まりで持つ。**"""

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("ページは 1 始まりで入れてください")


def _values_in(text: str) -> list[int]:
    out: list[int] = []
    for match in _INTEGER.finditer(text):
        token = match.group(1)
        if token[0] == "0":
            # `01` のような番号付け。**寸法として 0 で始まる書き方はしない。**
            continue
        value = int(token)
        if not (MIN_DIMENSION_MM <= value <= MAX_DIMENSION_MM):
            continue
        out.append(value)
    return out


def read_printed_dimensions(
    pdf_path: str | Path, page_number: int
) -> tuple[PrintedDimension, ...]:
    """1 ページぶんの寸法の候補。**同じ値が何度出ても、そのぶん並ぶ。**

    数を減らさないのは、**何回出てきたかが後で効く**ためである
    (1 回しか出ない数は寸法ではないかもしれない)。
    """
    import pymupdf

    with pymupdf.open(pdf_path) as document:
        if not (1 <= page_number <= len(document)):
            raise ValueError(
                f"ページ {page_number} はこの PDF({len(document)} ページ)にありません"
            )
        text = document[page_number - 1].get_text()
    return tuple(
        PrintedDimension(value, page_number) for value in _values_in(text)
    )


def distinct_values(dimensions: tuple[PrintedDimension, ...]) -> tuple[int, ...]:
    """出てきた値を、重複を除いて小さい順に。"""
    return tuple(sorted({d.value_mm for d in dimensions}))


__all__ = [
    "MAX_DIMENSION_MM",
    "METHOD_PRINTED_DIMENSION",
    "MIN_DIMENSION_MM",
    "PrintedDimension",
    "distinct_values",
    "read_printed_dimensions",
]
