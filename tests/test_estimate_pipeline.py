"""`estimating/pipeline.py` の回帰テスト。

**この層が無かったせいで、A-1 も A-2 も「見積の行が埋まるかどうかは
測っていない」で終わっていた。** 測れなかったのではなく、測る先が無かった。
2026-09-23 に確認したところ、`map_quantities` を呼ぶ本番の経路は 0 件で、
`app.py` は空だった。

守りたいのは 4 つ。

1. 図面の入口から見積の行の一覧まで、1 本でつながること
2. **「行が無い」で終わらせないこと。** 読めなかったのか、規則を
   持っていないのかを、`gaps()` に分けて出す
3. **金額を出さないこと**(単価をコードに持たない約束を変えない)
4. **何も自動で確定させないこと**
"""

from __future__ import annotations

import pytest

from estimating.pipeline import build_estimate_draft_from_quantities
from estimating.quantities import QuantityItem
from estimating.rules import load_rules, parse_rules

#: おーちゃんが挙げた、図面からは決まらない行。
FALLING_LINES = (
    "仮設水道料",
    "仮設電気料",
    "小運搬費",
    "荷上費",
    "墨出し",
    "竣工時清掃",
    "駐車場代",
)

EXAMPLE = "estimating/examples/synthetic_standing_rules.json"


def _area(value: float = 90.0) -> QuantityItem:
    return QuantityItem(
        target="施工床面積::ページ1",
        value_range=(value, value),
        unit="㎡",
        method_id="pdf_text_area",
    )


def test_ルールを与えると図面に無い行が7件とも出る() -> None:
    draft = build_estimate_draft_from_quantities((), load_rules(EXAMPLE))
    assert [line.work_item for line in draft.standing_lines] == list(FALLING_LINES)


def test_ルールが無いときは行が無い理由が残る() -> None:
    """**「工事にその行が無い」と「ルールを持っていない」を見分ける。**"""
    ruleset = parse_rules(
        {
            "format_version": 2,
            "ruleset_id": "ルールなし",
            "description": "架空",
            "rules": [],
            "standing_lines": [],
        }
    )
    draft = build_estimate_draft_from_quantities((), ruleset)
    assert draft.standing_lines == ()
    gaps = draft.gaps()
    assert len(gaps) == 1
    assert "ルール" in gaps[0]
    assert "工事にその行が無いからではなく" in gaps[0]


def test_もとになる数量が読めれば立て行に数量が入る() -> None:
    draft = build_estimate_draft_from_quantities((_area(90.0),), load_rules(EXAMPLE))
    by_name = {line.work_item: line for line in draft.standing_lines}
    # 見本の規則は 1㎡ あたりの値を null にしてある(**まだ決まっていない**)。
    # だから数量は入らず、理由が残る。
    assert by_name["竣工時清掃"].quantity_range is None
    assert any("決まっていない" in gap for gap in draft.gaps())


def test_金額を出さない() -> None:
    draft = build_estimate_draft_from_quantities((_area(),), load_rules(EXAMPLE))
    for line in draft.standing_lines:
        assert not hasattr(line, "amount")
        assert not hasattr(line, "unit_price")
    assert "金額は出していない" in draft.summary()


def test_何も自動で確定しない() -> None:
    draft = build_estimate_draft_from_quantities((_area(),), load_rules(EXAMPLE))
    assert draft.settled_lines == ()
    assert all(line.requires_human_confirmation for line in draft.standing_lines)


def test_規則が当たらない数量を捨てない() -> None:
    ruleset = load_rules(EXAMPLE)
    draft = build_estimate_draft_from_quantities((_area(),), ruleset)
    assert any("規則が当たらない" in gap for gap in draft.gaps())
    assert draft.quantities == (_area(),)
