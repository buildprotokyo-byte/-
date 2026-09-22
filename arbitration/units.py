"""数量の単位と、固定小数点整数への正規化。

v8 9.5節の「連続量(長さ・面積)への対応」を、**固定小数点の整数**として
実装する(`docs/decision_continuous_quantity_gap.md` 選択肢B、2026-09-21
おーちゃんの判断により採用)。

なぜ整数のままにするのか
------------------------
連続量を扱う素直な方法は `z3.Int` を `z3.Real` に替えることだが、
`killer_question/engine.py` の候補列挙が `range(lower, upper + 1)` である
ため、**連続値ではキラークエスチョンが原理的に動かなくなる**。
そこで「十分小さい単位の整数」として表す。長さは mm、面積は cm²、
金額は 円。こうすると小数が不要になり、ソルバーと
キラークエスチョンを**一切変更せずに**連続量を載せられる。

正規形と刻み
------------
=========== ========= ========================================
正規形       次元       意味
=========== ========= ========================================
``count``   個数       1個きざみ(従来からある唯一の単位)
``mm``      長さ       1mm きざみ
``cm2``     面積       1cm² きざみ(= 0.0001㎡)
``yen``     金額       1円きざみ
=========== ========= ========================================

別名は正規形への**倍率**を持つ(``m`` → ×1000 → ``mm``)。倍率の計算には
`Decimal` を使う。`12.5 * 1000` は偶然ぴったり 12500.0 になるが、
`0.1 * 1000` は 100.00000000000001 になり、float のままでは
「1mm きざみの整数」という約束を守れないため。

**粒度(`AxisEvidence.granularity`)はここでは扱わない。** 単位が同じでも
「対象要素1つ」と「工事1件」は比較できないため、別のフィールドとして
`arbitration/axis_quality_firewall.py` が持っている。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping

Dimension = Literal["count", "length", "area", "money"]


@dataclass(frozen=True)
class UnitSpec:
    """1つの正規形単位の仕様。"""

    canonical: str
    dimension: Dimension
    #: 1つ分の刻みを人に説明する文字列(エラーメッセージ・報告書用)。
    step_label: str
    #: この単位で許す最大値(正規形の整数として)。
    max_value: int
    #: 刻みが実用上の最小単位より細かく、レンジが広がりやすい単位か。
    #: True の単位は、キラークエスチョンの候補列挙を素直に行うと爆発する
    #: (`killer_question/engine.py` の列挙上限を参照)。
    is_fine_grained: bool


#: 正規形単位の一覧。
UNIT_SPECS: dict[str, UnitSpec] = {
    "count": UnitSpec(
        canonical="count", dimension="count", step_label="1個",
        max_value=1_000_000, is_fine_grained=False,
    ),
    "mm": UnitSpec(
        canonical="mm", dimension="length", step_label="1mm",
        # 1,000,000,000mm = 1000km。建築の延長として現実的な上限より十分大きい。
        max_value=1_000_000_000, is_fine_grained=True,
    ),
    "cm2": UnitSpec(
        canonical="cm2", dimension="area", step_label="1cm²(0.0001㎡)",
        # 1,000,000,000cm² = 100,000㎡ = 10ha。
        # 注: `docs/decision_continuous_quantity_gap.md` 2節で「床面積100㎡ を
        # cm² にすると 1,000,000 で従来の MAX_COUNT ちょうど」と書いたとおり、
        # 従来の一律上限では面積が入らない。単位ごとに上限を持たせて解消した。
        max_value=1_000_000_000, is_fine_grained=True,
    ),
    "yen": UnitSpec(
        canonical="yen", dimension="money", step_label="1円",
        max_value=1_000_000_000_000, is_fine_grained=True,
    ),
}

#: 入力に書かれうる単位表記 → (正規形, 正規形への倍率)。
#:
#: 倍率は Decimal の文字列で持つ。`0.1m` のような入力でも
#: 「1mm きざみの整数」を厳密に保つため。
UNIT_ALIASES: dict[str, tuple[str, str]] = {
    # --- 個数 ---
    "count": ("count", "1"),
    "件": ("count", "1"),
    "個": ("count", "1"),
    "本": ("count", "1"),
    "箇所": ("count", "1"),
    "数量": ("count", "1"),
    # --- 長さ ---
    "mm": ("mm", "1"),
    "ミリ": ("mm", "1"),
    "ミリメートル": ("mm", "1"),
    "cm": ("mm", "10"),
    "センチ": ("mm", "10"),
    "センチメートル": ("mm", "10"),
    "m": ("mm", "1000"),
    "メートル": ("mm", "1000"),
    # --- 面積 ---
    "cm2": ("cm2", "1"),
    "cm²": ("cm2", "1"),
    "m2": ("cm2", "10000"),
    "m²": ("cm2", "10000"),
    "㎡": ("cm2", "10000"),
    "平方メートル": ("cm2", "10000"),
    "平米": ("cm2", "10000"),
    # --- 金額 ---
    "yen": ("yen", "1"),
    "円": ("yen", "1"),
    "千円": ("yen", "1000"),
    "万円": ("yen", "10000"),
}


class UnitError(Exception):
    """単位の解釈・正規化に失敗したことを表す。

    ``code`` は `InferenceOrchestrator` がそのまま検証エラーの識別子として
    使う(``unknown_unit`` など)。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def canonical_unit(unit_raw: str) -> str:
    """表記から正規形の単位名を返す。未知なら ``UnitError``。"""
    try:
        return UNIT_ALIASES[unit_raw][0]
    except KeyError:
        raise UnitError("unknown_unit", unit_raw) from None


def spec_for(unit_raw: str) -> UnitSpec:
    """表記から正規形単位の仕様を返す。"""
    return UNIT_SPECS[canonical_unit(unit_raw)]


def is_alias_of_canonical(unit_raw: str) -> bool:
    """その表記が、正規形そのものか(倍率1で名前も一致するか)。"""
    entry = UNIT_ALIASES.get(unit_raw)
    return entry is not None and entry[0] == unit_raw


def normalise_value(unit_raw: str, value: object) -> int:
    """1つの数量を、正規形単位の整数に直す。

    ``12.5`` と ``"m"`` を渡すと ``12500`` を返す。刻みで割り切れない値
    (``12.55mm`` のように 1mm より細かい長さ)は、黙って丸めずに
    ``UnitError("value_finer_than_unit_step")`` を投げる。丸めてよいかは
    数量の種類ごとの判断になるため、ここで勝手に決めない。
    """
    try:
        target, factor = UNIT_ALIASES[unit_raw]
    except KeyError:
        raise UnitError("unknown_unit", unit_raw) from None

    # 文字列は受け付けない。ここは外部入力の境界なので、"5" のような値を
    # 黙って 5 と解釈すると型の緩さがそのまま下流へ漏れる。
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise UnitError("invalid_quantity", repr(value))
    try:
        # float は str 経由で Decimal にする。Decimal(0.1) が
        # 0.1000000000000000055511151231257827 になるのを避けるため。
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise UnitError("invalid_quantity", repr(value)) from None
    if not amount.is_finite():
        raise UnitError("invalid_quantity", repr(value))

    scaled = amount * Decimal(factor)
    if scaled != scaled.to_integral_value():
        spec = UNIT_SPECS[target]
        raise UnitError(
            "value_finer_than_unit_step",
            f"{value}{unit_raw} は {spec.step_label} きざみで表せません",
        )
    return int(scaled)


def normalise_range(
    unit_raw: str, lower: object, upper: object
) -> tuple[str, tuple[int, int]]:
    """レンジを正規形単位の整数レンジに直し、上下限と最大値を検査する。

    返り値は ``(正規形の単位名, (下限, 上限))``。
    """
    low = normalise_value(unit_raw, lower)
    high = normalise_value(unit_raw, upper)
    target = UNIT_ALIASES[unit_raw][0]
    spec = UNIT_SPECS[target]
    if low < 0 or high < 0:
        raise UnitError("negative_quantity", f"({low}, {high})")
    if low > high:
        raise UnitError("reversed_range", f"({low}, {high})")
    if high > spec.max_value:
        raise UnitError(
            "quantity_too_large",
            f"{high}{spec.canonical} は上限 {spec.max_value}{spec.canonical} を超えています",
        )
    return target, (low, high)


def same_dimension(units: Mapping[str, object] | list[str] | tuple[str, ...]) -> bool:
    """渡された単位がすべて同じ次元か。

    **同じ次元でも、単位が違えば比較してはならない**(mm と cm2 は次元が
    違うので当然だが、正規形が揃っていない限り数値は比較できない)。
    この関数は報告文の補助にだけ使い、照合の可否判定には使わない。
    """
    names = list(units)
    dimensions = set()
    for name in names:
        spec = UNIT_SPECS.get(name)
        if spec is None:
            return False
        dimensions.add(spec.dimension)
    return len(dimensions) <= 1
