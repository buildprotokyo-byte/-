"""案件の前提の層(`estimating/case_premises.py`)のテスト。

3層(読み取った数量／案件の前提／会社のルール)のうち、前提が層として無かった
ので足した。固定したい約束は 7 つ。

1. **前提のファイルは、規則のファイルとは別**であること(おーちゃんの回答13:
   規則は会社で1つ、案件ごとの前提は別のファイル)。
2. **仮説には「何が分かれば要らなくなるか」が必ず書いてある**こと。
   書けない仮説は、置いたことに誰も気づけない。
3. **人が入れた前提には、入れた人が残っている**こと。
4. **知らない項目と知らない版は断る**こと。
5. 前提を結びつけても、**値は1つも変わらない**こと。
6. 仮説に寄りかかった数量は**「仮説に基づく」**になること。
7. **前提そのものは何も確定させない**こと。確定させるのは仲裁層である。

**前提はすべて合成である。** 実案件の前提はリポジトリに置かない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estimating.basis import BASIS_HYPOTHETICAL, BASIS_INFERRED
from estimating.case_premises import (
    PREMISES_FORMAT_VERSION,
    CasePremise,
    PremiseError,
    PremiseScope,
    PremiseSet,
    apply_premises,
    load_premises,
    parse_premises,
)
from estimating.mapping import map_quantities
from estimating.quantities import QuantityItem
from estimating.rules import RULES_FORMAT_VERSION, parse_rules

EXAMPLE_PREMISES = (
    Path(__file__).resolve().parents[1]
    / "estimating"
    / "examples"
    / "synthetic_premises.json"
)


def _quantity(target: str = "建具数量::AW-1", **overrides) -> QuantityItem:
    kwargs = dict(
        target=target,
        value_range=(3.0, 3.0),
        unit="箇所",
        method_id="pdf_table_door_schedule",
        tier=3,
        action="requires_review",
    )
    kwargs.update(overrides)
    return QuantityItem(**kwargs)


def _payload(premises: list[dict], **overrides) -> dict:
    payload = {
        "format_version": PREMISES_FORMAT_VERSION,
        "premise_set_id": "test-synthetic",
        "premises": premises,
    }
    payload.update(overrides)
    return payload


def _hypothesis(**overrides) -> dict:
    premise = {
        "premise_id": "unit-mm",
        "kind": "寸法の単位",
        "statement": "単位が書かれていないため mm として読む。",
        "source": "hypothesis",
        "resolved_by": "基準点から求めた実寸と突き合わせる",
    }
    premise.update(overrides)
    return premise


# ---------------------------------------------------------------------------
# 1. 前提のファイルは規則のファイルとは別
# ---------------------------------------------------------------------------


def test_the_example_premises_load_and_are_marked_synthetic() -> None:
    premise_set = load_premises(EXAMPLE_PREMISES)
    assert premise_set.premise_set_id == "synthetic-example"
    assert "合成" in (premise_set.description or "")
    assert len(premise_set.hypotheses()) == 2


def test_a_rules_file_is_not_accepted_as_a_premises_file() -> None:
    """会社のルールと案件の前提を、同じファイルに混ぜない。"""
    rules_payload = {
        "format_version": RULES_FORMAT_VERSION,
        "ruleset_id": "company",
        "rules": [
            {
                "rule_id": "door",
                "kind": "建具数量",
                "line_items": [{"work_item": "建具 取付費", "unit": "箇所"}],
            }
        ],
    }
    with pytest.raises(PremiseError):
        parse_premises(rules_payload)

    # 逆向きも通らないこと。
    with pytest.raises(Exception):
        parse_rules(_payload([_hypothesis()]))


# ---------------------------------------------------------------------------
# 2〜3. 仮説と人の入力に要るもの
# ---------------------------------------------------------------------------


def test_a_hypothesis_must_say_what_would_make_it_unnecessary() -> None:
    with pytest.raises(PremiseError, match="何が分かれば"):
        parse_premises(_payload([_hypothesis(resolved_by="")]))


def test_a_human_premise_must_say_who_entered_it() -> None:
    with pytest.raises(PremiseError, match="入れた人"):
        parse_premises(
            _payload(
                [
                    {
                        "premise_id": "area-basis",
                        "kind": "面積の基準",
                        "statement": "施工床面積を基準にする。",
                        "source": "human",
                    }
                ]
            )
        )


def test_only_a_hypothesis_may_carry_alternatives() -> None:
    with pytest.raises(PremiseError, match="仮説のときだけ"):
        parse_premises(
            _payload(
                [
                    {
                        "premise_id": "area-basis",
                        "kind": "面積の基準",
                        "statement": "施工床面積を基準にする。",
                        "source": "human",
                        "entered_by": "見本",
                        "alternatives": ["専有延床面積にする"],
                    }
                ]
            )
        )


def test_a_premise_needs_a_statement_a_person_can_read() -> None:
    with pytest.raises(PremiseError):
        parse_premises(_payload([_hypothesis(statement="   ")]))


# ---------------------------------------------------------------------------
# 4. 知らない項目と知らない版は断る
# ---------------------------------------------------------------------------


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(PremiseError, match="知らない項目"):
        parse_premises(_payload([_hypothesis(confidence=0.9)]))


def test_an_unknown_field_on_the_file_is_refused() -> None:
    with pytest.raises(PremiseError, match="知らない項目"):
        parse_premises(_payload([_hypothesis()], notes="あとで消す"))


def test_an_unknown_format_version_is_refused() -> None:
    with pytest.raises(PremiseError, match="format_version"):
        parse_premises(_payload([_hypothesis()], format_version=2))


def test_a_duplicate_premise_id_is_refused() -> None:
    with pytest.raises(PremiseError, match="重複"):
        parse_premises(_payload([_hypothesis(), _hypothesis()]))


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(PremiseError, match="source"):
        parse_premises(_payload([_hypothesis(source="たぶん人")]))


def test_a_missing_premises_file_is_refused_with_its_path(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-file.json"
    with pytest.raises(PremiseError, match=str(missing.name)):
        load_premises(missing)


def test_a_broken_premises_file_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{これは JSON ではない", encoding="utf-8")
    with pytest.raises(PremiseError, match="JSON"):
        load_premises(broken)


# ---------------------------------------------------------------------------
# 5〜6. 数量に結びつける
# ---------------------------------------------------------------------------


def _set(premises: list[dict]) -> PremiseSet:
    return parse_premises(_payload(premises))


def test_a_case_wide_premise_covers_everything() -> None:
    premise_set = _set([_hypothesis()])
    (quantity,) = apply_premises([_quantity()], premise_set)
    assert quantity.premise_ids == ("unit-mm",)
    assert quantity.hypothesis_premise_ids == ("unit-mm",)
    assert quantity.basis == BASIS_HYPOTHETICAL


def test_a_scoped_premise_only_covers_its_own_kind() -> None:
    premise_set = _set([_hypothesis(scope={"target_kinds": ["建具数量"]})])
    door, area = apply_premises(
        [_quantity(), _quantity(target="専有延床面積", unit="㎡")], premise_set
    )
    assert door.hypothesis_premise_ids == ("unit-mm",)
    assert area.premise_ids == ()
    assert area.basis == BASIS_INFERRED


def test_a_scoped_premise_can_name_one_target() -> None:
    premise_set = _set([_hypothesis(scope={"targets": ["建具数量::AW-2"]})])
    first, second = apply_premises(
        [_quantity("建具数量::AW-1"), _quantity("建具数量::AW-2")], premise_set
    )
    assert first.premise_ids == ()
    assert second.premise_ids == ("unit-mm",)


def test_applying_premises_changes_no_value() -> None:
    """**値は1つも変えない。** 変えるのは前提の結びつきだけ。"""
    before = _quantity()
    (after,) = apply_premises([before], _set([_hypothesis()]))
    assert after.value_range == before.value_range
    assert after.unit == before.unit
    assert after.tier == before.tier
    assert after.action == before.action
    assert after.confirmed_range == before.confirmed_range
    assert after.derivation == before.derivation


def test_applying_premises_twice_does_not_pile_them_up() -> None:
    premise_set = _set([_hypothesis()])
    once = apply_premises([_quantity()], premise_set)
    twice = apply_premises(once, premise_set)
    assert twice[0].premise_ids == ("unit-mm",)


def test_premises_already_on_a_quantity_are_kept() -> None:
    """入口で付いた前提を、ここで消さない。"""
    quantity = _quantity(premise_ids=("入口で付いた前提",))
    (result,) = apply_premises([quantity], _set([_hypothesis()]))
    assert result.premise_ids == ("入口で付いた前提", "unit-mm")


def test_an_inferred_premise_does_not_make_the_value_hypothetical() -> None:
    premise_set = _set(
        [
            {
                "premise_id": "page-kind",
                "kind": "ページの種類",
                "statement": "3ページ目は建具表だと読み取った。",
                "source": "inferred",
            }
        ]
    )
    (quantity,) = apply_premises([_quantity()], premise_set)
    assert quantity.premise_ids == ("page-kind",)
    assert quantity.hypothesis_premise_ids == ()
    assert quantity.basis == BASIS_INFERRED


# ---------------------------------------------------------------------------
# 7. 前提そのものは何も確定させない
# ---------------------------------------------------------------------------


def test_a_premise_settles_nothing() -> None:
    ruleset = parse_rules(
        {
            "format_version": RULES_FORMAT_VERSION,
            "ruleset_id": "test-premises",
            "rules": [
                {
                    "rule_id": "door-install",
                    "kind": "建具数量",
                    "line_items": [{"work_item": "建具 取付費", "unit": "箇所"}],
                }
            ],
        }
    )
    quantities = apply_premises([_quantity()], _set([_hypothesis()]))
    result = map_quantities(quantities, ruleset)
    assert result.settled_lines() == ()
    (line,) = result.mappings[0].lines
    assert line.basis == BASIS_HYPOTHETICAL
    assert line.hypothesis_premise_ids == ("unit-mm",)
    # 原則10: その仮説に依存している行だけを引ける。
    assert result.lines_depending_on("unit-mm") == (line,)


def test_the_hypotheses_can_be_listed_for_a_person() -> None:
    premise_set = load_premises(EXAMPLE_PREMISES)
    for premise in premise_set.hypotheses():
        assert premise.resolved_by, "仮説なのに、何が分かれば要らなくなるかが無い"
        assert premise.statement


def test_a_premise_scope_with_nothing_set_is_case_wide() -> None:
    assert PremiseScope().is_case_wide
    assert not PremiseScope(target_kinds=("建具数量",)).is_case_wide


def test_a_premise_can_be_built_directly_without_a_file() -> None:
    premise = CasePremise(
        premise_id="p1",
        kind="天井高",
        statement="2400mm と置く。",
        source="hypothesis",
        resolved_by="展開図から読む",
    )
    assert premise.is_hypothesis
    assert PremiseSet("s", PREMISES_FORMAT_VERSION, (premise,)).get("p1") is premise
    assert PremiseSet("s", PREMISES_FORMAT_VERSION, (premise,)).get("p2") is None
