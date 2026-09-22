"""各値の「何に基づくか」(`estimating/basis.py`)のテスト。

おーちゃんの原則5「前提が揃うまで止めるのではなく、何に基づくかを区別して
出す：確定／推論に基づく／仮説に基づく／現地確認が必要」を、**見積の行まで
運べること**を固定する。

固定したい約束は 6 つ。

1. **仲裁層の由来(`read` / `derived` / `assumed`)が見積の行まで運ばれる**こと。
   `estimating/from_intake.py` がこれを写していなかったので、仮説から来た
   数量と図面から読んだ数量が行の上で見分けられなかった。
2. **「確定」は仲裁層が確定させた値だけ**であること。この層は格上げしない。
3. **「一般則で補った」値は「仮説に基づく」**になること。読んだ値と同じ扱いに
   しない(`assumed` は階層1に入らないという既存の約束と同じ向き)。
4. **弱いほうが勝つ**こと。1 つの行が複数の基づきから来たら、いちばん弱い
   ものになる(`axes/reading/protocol.py` の「根拠に assumed が1つでもあれば
   assumed に落ちる」と同じ向き)。
5. **見積の行が「何に基づくか」を持つ**こと。行を見ただけで分かること。
6. **既存の判定を変えていない**こと。確定の 3 条件はそのままで、
   基づきを足したことで確定する数量が増えない。
"""

from __future__ import annotations

import pytest

from estimating.basis import (
    BASIS_CONFIRMED,
    BASIS_HYPOTHETICAL,
    BASIS_INFERRED,
    BASIS_NEEDS_SITE_SURVEY,
    BASIS_STRONGEST_FIRST,
    weakest,
)
from estimating.from_intake import quantities_from_intake
from estimating.mapping import map_quantities
from estimating.quantities import QuantityItem
from estimating.rules import RULES_FORMAT_VERSION, parse_rules


# ---------------------------------------------------------------------------
# 合成の材料
# ---------------------------------------------------------------------------


class _Finding:
    """入口の `DrawingFinding` の形だけをまねた合成の読み。"""

    def __init__(self, target: str, derivation: str = "read", basis: tuple = ()) -> None:
        self.target = target
        self.value_range = (3.0, 3.0)
        self.unit = "箇所"
        self.method_id = "pdf_table_door_schedule"
        self.source_kind = "drawing"
        self.axis_id = "image"
        self.derivation = derivation
        self.derivation_basis = basis
        self.provenance = {}


class _Decision:
    def __init__(self, tier: int, action: str, confirmed_range=None) -> None:
        self.target = ""
        self.tier = tier
        self.action = action
        self.confirmed_range = confirmed_range


class _Result:
    def __init__(self, findings, decisions=()) -> None:
        self.findings = tuple(findings)
        self.decisions = tuple(decisions)
        self.door_schedule_rows = ()


def _quantity(**overrides) -> QuantityItem:
    kwargs = dict(
        target="建具数量::AW-1",
        value_range=(3.0, 3.0),
        unit="箇所",
        method_id="pdf_table_door_schedule",
        tier=3,
        action="requires_review",
    )
    kwargs.update(overrides)
    return QuantityItem(**kwargs)


def _door_ruleset():
    return parse_rules(
        {
            "format_version": RULES_FORMAT_VERSION,
            "ruleset_id": "test-basis",
            "rules": [
                {
                    "rule_id": "door-install",
                    "kind": "建具数量",
                    "line_items": [
                        {"work_item": "建具 取付費", "unit": "箇所"},
                    ],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# 1. 由来が見積の行まで運ばれる
# ---------------------------------------------------------------------------


def test_the_derivation_is_carried_from_the_intake_finding() -> None:
    """**写し忘れの修正。** 仲裁層にある由来が数量まで来ていなかった。"""
    result = _Result([_Finding("建具数量::AW-1", derivation="assumed")])
    (quantity,) = quantities_from_intake(result)
    assert quantity.derivation == "assumed", (
        "一般則で補った値が、図面から読んだ値と区別できないまま下流へ行っている"
    )


def test_a_derived_value_keeps_the_basis_of_its_own_sources() -> None:
    """計算した値は、根拠に「一般則で補った」が混じれば仮説に落ちる。"""
    result = _Result(
        [_Finding("建具数量::AW-1", derivation="derived", basis=("read", "assumed"))]
    )
    (quantity,) = quantities_from_intake(result)
    assert quantity.effective_derivation == "assumed"
    assert quantity.basis == BASIS_HYPOTHETICAL


# ---------------------------------------------------------------------------
# 2〜3. 4 つの区別
# ---------------------------------------------------------------------------


def test_only_the_arbitration_layer_makes_a_value_confirmed() -> None:
    confirmed = _quantity(tier=1, action="auto_confirm", confirmed_range=(3, 3))
    assert confirmed.basis == BASIS_CONFIRMED

    # 当てはめが一意でも、仲裁層が確定させていなければ確定にしない。
    assert _quantity().basis != BASIS_CONFIRMED


def test_a_value_filled_in_from_a_general_rule_is_hypothetical() -> None:
    assert _quantity(derivation="assumed").basis == BASIS_HYPOTHETICAL


def test_a_value_that_depends_on_a_hypothesis_premise_is_hypothetical() -> None:
    quantity = _quantity(
        premise_ids=("面積の基準",), hypothesis_premise_ids=("面積の基準",)
    )
    assert quantity.basis == BASIS_HYPOTHETICAL
    assert quantity.hypothesis_premise_ids == ("面積の基準",)


def test_a_read_value_that_is_not_confirmed_is_inferred_not_confirmed() -> None:
    """読めただけの値を「確定」にしない。**確定は仲裁層だけが作る。**"""
    assert _quantity(derivation="read").basis == BASIS_INFERRED


def test_a_value_that_needs_a_site_survey_says_why() -> None:
    """原則8: システムが**根拠を付けて**提案し、人が確認する。"""
    quantity = _quantity(site_survey_reason="図面に天井高の記載が無い")
    assert quantity.basis == BASIS_NEEDS_SITE_SURVEY
    assert quantity.site_survey_reason == "図面に天井高の記載が無い"


def test_a_confirmed_value_cannot_be_asked_for_a_site_survey_by_accident() -> None:
    """確定した値に現地確認の理由を付けたら、**弱いほうを採る。**"""
    quantity = _quantity(
        tier=1,
        action="auto_confirm",
        confirmed_range=(3, 3),
        site_survey_reason="図面に記載が無い",
    )
    assert quantity.basis == BASIS_NEEDS_SITE_SURVEY


# ---------------------------------------------------------------------------
# 4. 弱いほうが勝つ
# ---------------------------------------------------------------------------


def test_the_order_runs_from_strongest_to_weakest() -> None:
    assert BASIS_STRONGEST_FIRST == (
        BASIS_CONFIRMED,
        BASIS_INFERRED,
        BASIS_HYPOTHETICAL,
        BASIS_NEEDS_SITE_SURVEY,
    )


def test_the_weakest_basis_wins() -> None:
    assert weakest([BASIS_CONFIRMED, BASIS_HYPOTHETICAL]) == BASIS_HYPOTHETICAL
    assert weakest([BASIS_INFERRED, BASIS_NEEDS_SITE_SURVEY]) == BASIS_NEEDS_SITE_SURVEY
    assert weakest([BASIS_CONFIRMED]) == BASIS_CONFIRMED


def test_weakest_refuses_an_empty_list() -> None:
    """**何も無いときに既定を返さない。** 既定は静かに嘘をつく。"""
    with pytest.raises(ValueError):
        weakest([])


def test_weakest_refuses_a_value_it_does_not_know() -> None:
    with pytest.raises(ValueError):
        weakest([BASIS_CONFIRMED, "たぶん確定"])


# ---------------------------------------------------------------------------
# 5. 見積の行が「何に基づくか」を持つ
# ---------------------------------------------------------------------------


def test_the_estimate_line_carries_the_basis() -> None:
    result = map_quantities([_quantity(derivation="assumed")], _door_ruleset())
    (line,) = result.mappings[0].lines
    assert line.basis == BASIS_HYPOTHETICAL, (
        "行を見ただけでは、その数量が仮説から来たことが分からない"
    )


def test_the_estimate_line_carries_which_hypotheses_it_depends_on() -> None:
    """原則5: 仮説に基づく行は、どの仮説に依存しているかを記録する。"""
    quantity = _quantity(
        premise_ids=("面積の基準",), hypothesis_premise_ids=("面積の基準",)
    )
    result = map_quantities([quantity], _door_ruleset())
    (line,) = result.mappings[0].lines
    assert line.hypothesis_premise_ids == ("面積の基準",)
    assert line.premise_ids == ("面積の基準",)


# ---------------------------------------------------------------------------
# 6. 既存の判定を変えていない
# ---------------------------------------------------------------------------


def test_adding_the_basis_does_not_settle_anything_new() -> None:
    """基づきを足したことで、確定する数量が増えていないこと。"""
    ruleset = _door_ruleset()
    for derivation in ("read", "derived", "assumed"):
        basis = ("read",) if derivation == "derived" else ()
        quantity = _quantity(derivation=derivation, derivation_basis=basis)
        result = map_quantities([quantity], ruleset)
        assert result.settled_lines() == ()
        assert not result.mappings[0].settled


def test_a_confirmed_quantity_still_settles_exactly_as_before() -> None:
    """確定の 3 条件は変えていない。"""
    quantity = _quantity(tier=1, action="auto_confirm", confirmed_range=(3, 3))
    result = map_quantities([quantity], _door_ruleset())
    assert result.mappings[0].settled
    assert len(result.settled_lines()) == 1
    assert result.settled_lines()[0].basis == BASIS_CONFIRMED


# ---------------------------------------------------------------------------
# 7. 前提を差し替えたときに作り直す行を引ける(原則10)
# ---------------------------------------------------------------------------


def test_the_lines_depending_on_a_premise_can_be_looked_up() -> None:
    depends = _quantity(
        target="建具数量::AW-1",
        premise_ids=("面積の基準",),
        hypothesis_premise_ids=("面積の基準",),
    )
    independent = _quantity(target="建具数量::AW-2")
    result = map_quantities([depends, independent], _door_ruleset())

    affected = result.lines_depending_on("面積の基準")
    assert [line.source_target for line in affected] == ["建具数量::AW-1"]
    assert result.lines_depending_on("知らない前提") == ()


def test_the_summary_shows_what_the_lines_rest_on() -> None:
    result = map_quantities(
        [
            _quantity(target="建具数量::AW-1", derivation="assumed"),
            _quantity(target="建具数量::AW-2"),
        ],
        _door_ruleset(),
    )
    text = result.summary()
    assert "何に基づくか:" in text
    assert f"{BASIS_INFERRED} 1 件" in text
    assert f"{BASIS_HYPOTHETICAL} 1 件" in text


# ---------------------------------------------------------------------------
# 8. 同じ次元の単位換算は許す(おーちゃんの回答12)。次元が変わる換算はしない
# ---------------------------------------------------------------------------


def test_a_rule_may_state_its_line_in_another_unit_of_the_same_dimension() -> None:
    """mm で読んだ長さを、m で立てた行に当てられること。**既にそうなっている。**"""
    ruleset = parse_rules(
        {
            "format_version": RULES_FORMAT_VERSION,
            "ruleset_id": "test-units",
            "rules": [
                {
                    "rule_id": "wall-length",
                    "kind": "壁長",
                    "line_items": [{"work_item": "壁 下地", "unit": "m"}],
                }
            ],
        }
    )
    quantity = _quantity(target="壁長::ページ1", value_range=(3500.0, 3500.0), unit="mm")
    result = map_quantities([quantity], ruleset)
    (line,) = result.mappings[0].lines
    assert line.unit == "m"
    assert line.value_range == (3.5, 3.5), "同じ次元の換算が厳密に効いていない"


def test_a_rule_may_not_change_the_dimension() -> None:
    """長さから面積は**換算ではなく計算**なので、当てはめでは作らない。"""
    ruleset = parse_rules(
        {
            "format_version": RULES_FORMAT_VERSION,
            "ruleset_id": "test-units-2",
            "rules": [
                {
                    "rule_id": "wall-area",
                    "kind": "壁長",
                    "line_items": [{"work_item": "壁 仕上", "unit": "㎡"}],
                }
            ],
        }
    )
    quantity = _quantity(target="壁長::ページ1", value_range=(3500.0, 3500.0), unit="mm")
    result = map_quantities([quantity], ruleset)
    assert result.mappings[0].status == "unmapped"
    assert any("換算しない" in reason for reason in result.mappings[0].reasons)
