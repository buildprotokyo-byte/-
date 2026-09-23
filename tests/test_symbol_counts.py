"""14周目: 人が数えた記号の個数を受け取る口と、それを数量に直す層のテスト。

**合成データだけを使う。** 実案件の図面・見積明細・そこから作った値は出てこない。
"""

from __future__ import annotations

import pytest

from arbitration.method_policies import (
    DEFAULT_METHOD_POLICIES,
    METHOD_HUMAN_SYMBOL_COUNT,
)
from estimating.from_symbol_counts import (
    HUMAN_COUNT_NOTE,
    KIND_SYMBOL_COUNT,
    NO_PAGE_NOTE,
    SOURCE_HUMAN_INPUT,
    ZERO_NOTE,
    quantities_from_symbol_counts,
)
from estimating.mapping import map_quantities
from estimating.rules import load_rules
from intake.symbol_counts import (
    COUNTABLE_UNITS,
    MAX_COUNT,
    SymbolCount,
    SymbolCountError,
)

SYNTHETIC_RULES = "estimating/examples/synthetic_symbol_rules.json"


# ---------------------------------------------------------------- 受け取る口


def test_名前が空なら止まる():
    with pytest.raises(SymbolCountError, match="記号の名前"):
        SymbolCount("   ", 3)


def test_個数を入れなくても作れる():
    entry = SymbolCount("コンセント")
    assert entry.count is None
    assert entry.has_count is False
    assert "個数" in entry.missing()


def test_ゼロは入れていないことと区別される():
    entry = SymbolCount("コンセント", 0)
    assert entry.has_count is True
    assert "個数" not in entry.missing()


def test_負の個数は止まる():
    # 「0 以上」の文言で止まること。**「整数で」とは別の文言である。**
    # 同じ文言だと、`count < 0` を `count < -1` に変えても気づけない。
    with pytest.raises(SymbolCountError, match="0 以上"):
        SymbolCount("コンセント", -1)


def test_整数でない個数は止まる():
    with pytest.raises(SymbolCountError, match="整数で"):
        SymbolCount("コンセント", 3.5)


def test_真偽値は個数として受け付けない():
    with pytest.raises(SymbolCountError, match="整数で"):
        SymbolCount("コンセント", True)


def test_多すぎる個数は止まる():
    SymbolCount("コンセント", MAX_COUNT)
    with pytest.raises(SymbolCountError, match="多すぎます"):
        SymbolCount("コンセント", MAX_COUNT + 1)


def test_上限は一万である():
    # 定数そのものを書く。`MAX_COUNT == MAX_COUNT` では書き換えを検出できない。
    assert MAX_COUNT == 10_000


def test_受け付ける単位は三つだけである():
    assert COUNTABLE_UNITS == ("箇所", "個", "本")


@pytest.mark.parametrize("unit", ["箇所", "個", "本"])
def test_数えられる単位は通る(unit):
    assert SymbolCount("コンセント", 3, unit=unit).unit == unit


def test_式は数えて出す単位ではないと言って止まる():
    # **`台`・`組` とは別の文言。** 同じ文言だと、
    # `UNCOUNTABLE_UNITS` を空にしても `COUNTABLE_UNITS` 側で止まって気づけない。
    with pytest.raises(SymbolCountError, match="数えて出す単位ではありません"):
        SymbolCount("仮設電気", 1, unit="式")


@pytest.mark.parametrize("unit", ["台", "組", "枚"])
def test_仕組みが知らない単位は読み替えずに止まる(unit):
    with pytest.raises(SymbolCountError, match="まだ知らない単位"):
        SymbolCount("換気扇", 2, unit=unit)


def test_知らない単位を箇所に読み替えない():
    # 読み替えてしまうと、2 個で 1 組のものが 2 箇所として下流へ流れる。
    with pytest.raises(SymbolCountError):
        SymbolCount("コンセント", 2, unit="組")


def test_ページは一始まりである():
    SymbolCount("コンセント", 3, page_number=1)
    with pytest.raises(SymbolCountError, match="1 以上"):
        SymbolCount("コンセント", 3, page_number=0)


def test_ページが整数でなければ止まる():
    with pytest.raises(SymbolCountError, match="整数で"):
        SymbolCount("コンセント", 3, page_number="22")


def test_知らない数え方は止まる():
    with pytest.raises(SymbolCountError, match="数え方"):
        SymbolCount("コンセント", 3, counted_with="なんとなく")


def test_ページを入れなければ足りない欄に出る():
    assert SymbolCount("コンセント", 3).missing() == ("ページ",)


# ---------------------------------------------------------------- 数量に直す


def test_空の入力からは何も作らない():
    result = quantities_from_symbol_counts([])
    assert result.quantities == ()
    assert result.gaps == ()


def test_個数が入っていなければ数量を作らず理由を残す():
    result = quantities_from_symbol_counts([SymbolCount("コンセント")])
    assert result.quantities == ()
    assert len(result.gaps) == 1
    assert "0 で埋めない" in result.gaps[0]


def test_個数から数量ができる():
    result = quantities_from_symbol_counts(
        [SymbolCount("コンセント", 12, page_number=22, counted_with="目視")]
    )
    (quantity,) = result.quantities
    assert quantity.target == f"{KIND_SYMBOL_COUNT}::コンセント"
    assert quantity.value_range == (12.0, 12.0)
    assert quantity.unit == "箇所"
    assert quantity.method_id == METHOD_HUMAN_SYMBOL_COUNT


def test_出どころは人の入力である():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    # 文字列そのものを書く。定数どうしを比べると書き換えを検出できない。
    assert result.quantities[0].source_kind == "human"
    assert SOURCE_HUMAN_INPUT == "human"
    assert result.quantities[0].axis_id == "human"


def test_由来は読みであって計算ではない():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    assert result.quantities[0].derivation == "read"


def test_人が数えた値であることを必ず注記する():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12, page_number=1)])
    assert HUMAN_COUNT_NOTE in result.quantities[0].notes


def test_ページの申告が無いときだけページの注記が付く():
    with_page = quantities_from_symbol_counts(
        [SymbolCount("コンセント", 12, page_number=22)]
    )
    without_page = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    assert NO_PAGE_NOTE not in with_page.quantities[0].notes
    assert NO_PAGE_NOTE in without_page.quantities[0].notes


def test_ゼロと申告されたら主張として注記する():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 0, page_number=1)])
    (quantity,) = result.quantities
    assert quantity.value_range == (0.0, 0.0)
    assert ZERO_NOTE in quantity.notes


def test_申告が無い属性は入れない():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    attributes = dict(result.quantities[0].attributes)
    assert attributes == {"記号名": "コンセント"}
    assert "ページ" not in attributes
    assert "数え方" not in attributes


def test_申告がある属性だけ入る():
    result = quantities_from_symbol_counts(
        [
            SymbolCount(
                "コンセント", 12, page_number=22, counted_with="目視", entered_by="おーちゃん"
            )
        ]
    )
    attributes = dict(result.quantities[0].attributes)
    assert attributes["ページ"] == "22"
    assert attributes["数え方"] == "目視"
    assert attributes["入れた人"] == "おーちゃん"


def test_同じ記号名が二回入っても自動で足さない():
    result = quantities_from_symbol_counts(
        [
            SymbolCount("TEL", 1, page_number=22),
            SymbolCount("TEL", 2, page_number=23),
        ]
    )
    assert len(result.quantities) == 2
    assert [q.value_range for q in result.quantities] == [(1.0, 1.0), (2.0, 2.0)]
    assert any("自動で足していない" in gap for gap in result.gaps)


def test_記号名が一回だけなら足していない理由は出ない():
    result = quantities_from_symbol_counts([SymbolCount("TEL", 1, page_number=22)])
    assert result.gaps == ()


def test_独立でないことを根拠に残す():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    note = result.quantities[0].provenance["independence_note"]
    assert "別のデータ源" in note
    assert "校正されていない" in note


def test_確定はこの層では付けない():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12)])
    quantity = result.quantities[0]
    assert quantity.tier is None
    assert quantity.action is None
    assert quantity.confirmed_range is None


# ---------------------------------------------------------------- 登録簿


def test_手法は未校正で上限はweak():
    # **ここを校正済みにすると、人が 1 回入れた値と印字の一致だけで
    # 階層1に届いてしまう。** 登録簿を直接読んで固定する。
    policy = DEFAULT_METHOD_POLICIES[METHOD_HUMAN_SYMBOL_COUNT]
    assert policy.calibrated is False
    assert policy.max_strength == "weak"


def test_手法IDの文字列が変わっていない():
    assert METHOD_HUMAN_SYMBOL_COUNT == "human_symbol_count"


# ---------------------------------------------------------------- 見積の行まで


def _mapped_codes(entries):
    result = quantities_from_symbol_counts(entries)
    ruleset = load_rules(SYNTHETIC_RULES)
    mapping = map_quantities(result.quantities, ruleset)
    return [
        line.code
        for item in mapping.mappings
        for outcome in item.outcomes
        for line in outcome.lines
    ]


def test_四つの記号が合成の規則で見積の行に当たる():
    codes = _mapped_codes(
        [
            SymbolCount("コンセント", 12, page_number=22),
            SymbolCount("スイッチ", 7, page_number=22),
            SymbolCount("配線", 3, page_number=22),
            SymbolCount("TEL", 1, page_number=22),
        ]
    )
    assert codes == ["SYN-E-001", "SYN-E-002", "SYN-E-003", "SYN-E-004"]


def test_規則に無い記号名は行に当たらない():
    assert _mapped_codes([SymbolCount("ぬるぽ", 5, page_number=22)]) == []


def test_行に当たっても数量は確定しない():
    result = quantities_from_symbol_counts([SymbolCount("コンセント", 12, page_number=22)])
    ruleset = load_rules(SYNTHETIC_RULES)
    mapping = map_quantities(result.quantities, ruleset)
    lines = [
        line
        for item in mapping.mappings
        for outcome in item.outcomes
        for line in outcome.lines
    ]
    assert lines
    assert all(line.is_confirmed_quantity is False for line in lines)
