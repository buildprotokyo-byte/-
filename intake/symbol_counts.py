"""人が図面を見て数えた「記号の種類と個数」を受け取る。

2026-09-23、14周目。**画面(クリックの操作)は作らない。**
`intake/room_dimensions.py`(9周目)と同じく、関数の入力として受け取るところまで。

なぜ人に数えてもらうのか
------------------------
10〜13 周で、この図面から記号を自動で数える道を 4 通り測って全部だめだった。

- 10 周目: 繰り返す図形を群にまとめる → 674 群。正解の整数 1〜24 とは桁が違い、
  **でたらめな個数のほうがよく当たった**(`docs/a1_real_drawing_symbol_report.md`)。
- 11 周目: 表の升目で凡例を切り出す → 図面の線を表と誤認。でっち上げが増えた。
- 12 周目: 1 行に図形が 1 個 → でっち上げは消えたが残りが 6 件。
- 13 周目(キラークエスチョン): **記号の名前は自動で取れない。**
  入れ替え対照 12 件に対し本測定 15 件で、差が無かった
  (`docs/a1_legend_killer_question_report.md`)。

**この図面に電気の機器表は無い**(14 周目に 34 ページ全部の文字を走査して確認。
「機器表」「器具表」「姿図」「個数」はどのページにも出てこない)。
図面の印字から拾う道も無い。**残ったのは人に入れてもらうことだけである。**

数えられない単位は受け取らない
------------------------------
`arbitration/units.py` が解釈できる個数の単位は `箇所` `個` `本` だけである。
**ここで受け付けてしまうと、下流の `QuantityItem` を作るところで落ちる。**
落ちる場所が遠いと原因が分からなくなるので、**入口で、単位を名指しして止める。**

- `式` … 数えて出す単位ではない。**個数では原理的に答えられない。**
- `台` `組` … 数えられるが、`arbitration/units.py` がまだ知らない。
  **勝手に `箇所` に読み替えない。** 読み替えると、2 個で 1 組のものが
  2 箇所として下流へ流れる。

**この 2 つは別の理由なので、別の文言で止める。**

独立性についての但し書き(重要)
------------------------------
人が数えた個数は、**図面とは別のデータ源**である。
`intake/start_kit.py` の基準点と同じで、ここを校正済みにした瞬間に
「人が 1 回入れた値」と「図面の印字」だけで階層1(自動確定)に届いてしまう。
登録簿(`arbitration/method_policies.py`)では **未校正・上限 weak** で固定する。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 人が数えた記号の個数の手法ID。実体はここにあるが、`intake` は `arbitration` を
#: import するので、登録簿側には文字列として置く(逆向きに import すると循環する)。
METHOD_HUMAN_SYMBOL_COUNT = "human_symbol_count"

#: 受け付ける個数の単位。**`arbitration/units.py` が解釈できるものだけ。**
COUNTABLE_UNITS: tuple[str, ...] = ("箇所", "個", "本")

#: 数えて出す単位ではないもの。**個数では原理的に答えられない。**
UNCOUNTABLE_UNITS: tuple[str, ...] = ("式",)

#: 1 つの記号について受け付ける個数の上限。
#: 上限を置くのは、**ありえない値だけを弾く**ためで、
#: もっともらしい取り違え(12 と 21 の入れ替え)はここでは捕まえられない。
MAX_COUNT = 10_000

#: どうやって数えたかの申告。**既定は「不明」。**
COUNT_METHODS: tuple[str, ...] = ("目視", "図面に印字", "現地", "不明")
COUNT_METHOD_UNKNOWN = "不明"


class SymbolCountError(Exception):
    """人が入れた記号の個数が、そのままでは使えない形だった。

    **黙って直さない。** 単位の読み替え(組 を 箇所 に)は、直した結果が
    もっともらしい数値になるので、後から気づけない。
    """


@dataclass(frozen=True)
class SymbolCount:
    """1 種類の記号についての、人が数えた結果。"""

    symbol_name: str
    """人が自分の言葉で書いた記号の名前(`コンセント` など)。**表記はそろえない。**"""

    count: int | None = None
    """数えた個数。**入れていないときは `None`。0 で埋めない。**

    `0` は「数えたが 1 つも無かった」という**主張**であって、
    「入れていない」とは別である。両方を受け取れるようにしてある。
    """

    unit: str = "箇所"
    """数えた単位。`COUNTABLE_UNITS` のどれか。"""

    page_number: int | None = None
    """どのページを数えたか(1 始まり)。**入れなくてよい。**"""

    entered_by: str = ""
    """誰が入れたか。人の入力であることを根拠に残すため。"""

    counted_with: str = COUNT_METHOD_UNKNOWN
    """どうやって数えたか。"""

    def __post_init__(self) -> None:
        if not self.symbol_name.strip():
            raise SymbolCountError("記号の名前を入れてください")
        _check_unit(self.unit)
        _check_count(self.count)
        _check_page(self.page_number)
        if self.counted_with not in COUNT_METHODS:
            raise SymbolCountError(
                f"数え方 {self.counted_with!r} は {list(COUNT_METHODS)} の"
                "どれかにしてください"
            )

    @property
    def has_count(self) -> bool:
        """個数が入っているか。**0 も入っているうちに数える。**"""
        return self.count is not None

    def missing(self) -> tuple[str, ...]:
        """入っていない欄の名前。**足りないものを黙らせないため。**"""
        out: list[str] = []
        if self.count is None:
            out.append("個数")
        if self.page_number is None:
            out.append("ページ")
        return tuple(out)


def _check_unit(unit: str) -> None:
    if unit in UNCOUNTABLE_UNITS:
        raise SymbolCountError(
            f"単位 {unit!r} は数えて出す単位ではありません。"
            "個数では答えられないので、別の経路(会社の決まりの行)で扱ってください"
        )
    if unit not in COUNTABLE_UNITS:
        raise SymbolCountError(
            f"単位 {unit!r} はこの仕組みがまだ知らない単位です"
            f"({list(COUNTABLE_UNITS)} のどれかにしてください)。"
            "**勝手に読み替えません。** 2 個で 1 組のものが 2 箇所として流れるためです"
        )


def _check_count(count: int | None) -> None:
    if count is None:
        return
    if isinstance(count, bool) or not isinstance(count, int):
        raise SymbolCountError(
            f"個数は整数で入れてください(入った値: {count!r})"
        )
    if count < 0:
        raise SymbolCountError(
            f"個数は 0 以上にしてください(入った値: {count})"
        )
    if count > MAX_COUNT:
        raise SymbolCountError(
            f"個数 {count} は多すぎます({MAX_COUNT} 超)。"
            "数える対象が違っていないか確かめてください"
        )


def _check_page(page_number: int | None) -> None:
    if page_number is None:
        return
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise SymbolCountError(
            f"ページは整数で入れてください(入った値: {page_number!r})"
        )
    if page_number < 1:
        raise SymbolCountError(
            f"ページは 1 以上で入れてください(入った値: {page_number})。"
            "**1 始まりです**(0 始まりの番号を入れると 1 ページずれます)"
        )


__all__ = [
    "COUNTABLE_UNITS",
    "COUNT_METHODS",
    "COUNT_METHOD_UNKNOWN",
    "MAX_COUNT",
    "METHOD_HUMAN_SYMBOL_COUNT",
    "SymbolCount",
    "SymbolCountError",
    "UNCOUNTABLE_UNITS",
]
