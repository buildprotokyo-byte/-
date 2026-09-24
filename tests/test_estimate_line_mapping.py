"""数量を見積の行に当てはめる仕組み(`estimating/`)のテスト。

**規則は合成である。** 実際の会社の積算ルールも、実案件の見積明細も
リポジトリに置かない決まりなので、このテストが使う規則はすべてこの中で
組み立てるか、`estimating/examples/` の見本(明示的に合成と書いてある)から
読む。

固定したい約束は 7 つ。

1. 当てはめの規則が**コードに埋め込まれていない**こと。差し替えられる
   外部ファイルとして読むこと。
2. 規則が**一意に決まらないときは候補として出し、確定しない**こと。
3. **規則が一意に決まっただけでは確定しない**こと。数量そのものが仲裁層で
   確定していることが同時に要る(単一の指標だけを根拠に自動確定させない)。
4. 規則が当たらなかった数量を**黙って捨てない**こと。
5. 同じ見積の行に複数の数量が当たったとき、**足さない**こと。入口が
   「ページをまたいで足さない」と決めているのを、ここで打ち消さない。
6. 単位の次元が合わない規則で**行を作らない**こと。勝手に換算しない。
7. 規則ファイルに知らない項目があったら**黙って読み飛ばさない**こと。
   Codex 側の仕様が届いたとき、半分だけ効いた状態にならないようにする。

あわせて、**入口から見積の行まで通しても 1 件も確定しない**ことを固定する。
2026-09-22 時点で入口が出す数量は全件が階層3(人の確認)なので、当てはめが
どれだけうまくいっても確定は 0 件になるはずである。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.schedule_tables import DoorScheduleRow, ScheduleCell
from axes.reading.meaning import PURPOSE_UNESTABLISHED, Meaning
from estimating.from_intake import quantities_from_intake
from estimating.mapping import map_quantities
from estimating.quantities import QuantityError, QuantityItem, split_target
from estimating.rules import (
    RULES_FORMAT_VERSION,
    RuleError,
    load_rules,
    parse_rules,
)
from intake.drawing_intake import IntakeConfig, read_drawing

from tests.test_drawing_intake import _scanned_page, _vector_plan_page

#: このリポジトリが同梱する**合成の**見本規則。
EXAMPLE_RULES = Path(__file__).resolve().parents[1] / "estimating" / "examples" / "synthetic_rules.json"


# ---------------------------------------------------------------------------
# 合成の規則と数量
# ---------------------------------------------------------------------------


def _ruleset_payload(rules: list[dict]) -> dict:
    return {
        "format_version": RULES_FORMAT_VERSION,
        "ruleset_id": "test-synthetic",
        "description": "テスト用の合成規則。実在の会社の積算ルールではない。",
        "rules": rules,
    }


def _door_rule(rule_id: str = "door-install", **overrides) -> dict:
    rule = {
        "rule_id": rule_id,
        "kind": "建具数量",
        "line_items": [
            {
                "code": "D-100",
                "work_item": "建具 取付費",
                "major_category": "木工事",
                "unit": "箇所",
            },
            {
                "code": "D-200",
                "work_item": "建具 材料費",
                "major_category": "木工事",
                "unit": "箇所",
            },
        ],
    }
    rule.update(overrides)
    return rule


def _door_quantity(
    mark: str = "AW-1",
    *,
    quantity: float = 3.0,
    attributes: dict[str, str] | None = None,
    action: str | None = "requires_review",
    tier: int | None = 3,
    confirmed_range: tuple[int, int] | None = None,
) -> QuantityItem:
    return QuantityItem(
        target=f"建具数量::{mark}",
        value_range=(quantity, quantity),
        unit="箇所",
        method_id="pdf_table_door_schedule",
        tier=tier,
        action=action,
        confirmed_range=confirmed_range,
        attributes=attributes or {},
    )


# ---------------------------------------------------------------------------
# 1. 規則はコードに埋め込まない
# ---------------------------------------------------------------------------


def test_the_target_name_splits_into_a_kind_and_a_key() -> None:
    """規則は `建具数量::AW-1` の全文ではなく、種類のほうに当てる。"""
    assert split_target("建具数量::AW-1") == ("建具数量", "AW-1")
    assert split_target("開き戸::ページ1") == ("開き戸", "ページ1")
    assert split_target("専有延床面積") == ("専有延床面積", None)


def _code_strings(module: Path) -> list[str]:
    """モジュールの中の、説明文ではない文字列リテラルを集める。

    説明のための文(モジュール・クラス・関数の説明文と、属性の下に置いた
    説明文)は除く。規則がコードに埋め込まれるなら、辞書やリストの中の
    文字列として現れるはずなので、そこだけを見る。
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    documentation: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for child in body:
            if (
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
            ):
                documentation.add(id(child.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def test_no_mapping_rule_is_written_into_the_code() -> None:
    """当てはめ先の名前(取付費など)がコードに一切書かれていないこと。

    ここが破れると「差し替えられる設定として持つ」が形だけになる。
    """
    package = Path(__file__).resolve().parents[1] / "estimating"
    modules = sorted(package.glob("*.py"))
    assert modules
    for module in modules:
        for literal in _code_strings(module):
            for forbidden in ("取付費", "材料費", "撤去費", "木工事", "内装工事"):
                assert forbidden not in literal, (
                    f"{module.name} に当てはめ先 {forbidden} が埋め込まれている"
                )


def test_the_shipped_example_ruleset_loads_and_says_it_is_synthetic() -> None:
    ruleset = load_rules(EXAMPLE_RULES)
    assert ruleset.rules
    assert "合成" in (ruleset.description or "")


# ---------------------------------------------------------------------------
# 7. 規則ファイルの検査
# ---------------------------------------------------------------------------


def test_a_rule_file_from_another_format_version_is_refused() -> None:
    """Codex 側の仕様が届いたとき、半分だけ読めてしまう事故を防ぐ。"""
    payload = _ruleset_payload([_door_rule()])
    payload["format_version"] = RULES_FORMAT_VERSION + 1
    with pytest.raises(RuleError) as excinfo:
        parse_rules(payload)
    assert "format_version" in str(excinfo.value)


def test_an_unknown_field_in_a_rule_is_refused_not_ignored() -> None:
    payload = _ruleset_payload([_door_rule(歩掛=1.2)])
    with pytest.raises(RuleError) as excinfo:
        parse_rules(payload)
    assert "歩掛" in str(excinfo.value)


def test_a_line_with_an_unknown_unit_is_refused_at_load_time() -> None:
    rule = _door_rule()
    rule["line_items"][0]["unit"] = "人工"
    with pytest.raises(RuleError) as excinfo:
        parse_rules(_ruleset_payload([rule]))
    assert "人工" in str(excinfo.value)


def test_two_rules_with_the_same_id_are_refused() -> None:
    payload = _ruleset_payload([_door_rule("same"), _door_rule("same")])
    with pytest.raises(RuleError):
        parse_rules(payload)


def test_a_rule_with_no_line_items_is_refused() -> None:
    rule = _door_rule()
    rule["line_items"] = []
    with pytest.raises(RuleError):
        parse_rules(_ruleset_payload([rule]))


# ---------------------------------------------------------------------------
# 2 / 3. 一意でなければ候補。一意でも数量が確定していなければ確定しない
# ---------------------------------------------------------------------------


def test_one_rule_expands_one_quantity_into_several_lines() -> None:
    """取付と材料のように、1 つの数量が複数の行になるのは「あいまい」ではない。"""
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    result = map_quantities([_door_quantity()], ruleset)

    (mapping,) = result.mappings
    assert mapping.status == "unique"
    (outcome,) = mapping.outcomes
    assert [line.work_item for line in outcome.lines] == ["建具 取付費", "建具 材料費"]
    assert all(line.value_range == (3.0, 3.0) for line in outcome.lines)


def test_two_matching_rules_become_candidates_and_nothing_is_settled() -> None:
    """当てはめが一意に決まらないとき、候補として出すが確定しない。"""
    ruleset = parse_rules(
        _ruleset_payload([_door_rule("wooden"), _door_rule("steel")])
    )
    result = map_quantities([_door_quantity()], ruleset)

    (mapping,) = result.mappings
    assert mapping.status == "ambiguous"
    assert {outcome.rule_id for outcome in mapping.outcomes} == {"wooden", "steel"}
    assert mapping.settled is False
    assert result.settled_lines() == ()


def test_rule_uniqueness_alone_does_not_settle_a_line() -> None:
    """**規則が一意に決まっただけでは確定しない。**

    数量そのものが仲裁層で確定している(階層1)ことが同時に要る。ここが
    片方だけで通ると「単一の指標だけを根拠に自動確定させない」が破れる。
    """
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))

    unconfirmed = _door_quantity(action="requires_review", tier=3)
    (mapping,) = map_quantities([unconfirmed], ruleset).mappings
    assert mapping.status == "unique"
    assert mapping.settled is False

    confirmed = _door_quantity(
        action="auto_confirm", tier=1, confirmed_range=(3, 3)
    )
    (mapping,) = map_quantities([confirmed], ruleset).mappings
    assert mapping.status == "unique"
    assert mapping.settled is True


def test_a_confirmed_quantity_that_is_ambiguous_is_still_not_settled() -> None:
    """もう片方だけでも確定しないこと(対称の壊し方)。"""
    ruleset = parse_rules(
        _ruleset_payload([_door_rule("wooden"), _door_rule("steel")])
    )
    confirmed = _door_quantity(action="auto_confirm", tier=1, confirmed_range=(3, 3))
    (mapping,) = map_quantities([confirmed], ruleset).mappings
    assert mapping.settled is False


def test_a_settled_line_uses_the_confirmed_range_not_the_reading() -> None:
    """確定している数量では、読んだ値ではなく仲裁層が確定した値を使う。"""
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    quantity = QuantityItem(
        target="建具数量::AW-1",
        value_range=(3.0, 4.0),
        unit="箇所",
        method_id="pdf_table_door_schedule",
        tier=1,
        action="auto_confirm",
        confirmed_range=(3, 3),
    )
    (mapping,) = map_quantities([quantity], ruleset).mappings
    (outcome,) = mapping.outcomes
    assert all(line.canonical_range == (3, 3) for line in outcome.lines)
    assert all(line.is_confirmed_quantity for line in outcome.lines)


# ---------------------------------------------------------------------------
# 4. 当たらなかった数量を捨てない
# ---------------------------------------------------------------------------


def test_a_quantity_no_rule_matches_is_kept_with_a_reason() -> None:
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    area = QuantityItem(
        target="専有延床面積",
        value_range=(95.54, 95.54),
        unit="㎡",
        method_id="pdf_text_area",
    )
    result = map_quantities([area], ruleset)

    (mapping,) = result.mappings
    assert mapping.status == "unmapped"
    assert mapping.outcomes == ()
    assert mapping.reasons
    assert result.unmapped() == (mapping,)


def test_an_attribute_condition_does_not_match_when_the_attribute_is_unreadable() -> None:
    """種別が読めなかった建具に、種別で条件を書いた規則を当てない。

    読めなかったものを「当たらない」と扱い、理由を残す。当ててしまうと
    読めていない属性で見積の行が決まる。
    """
    ruleset = parse_rules(
        _ruleset_payload([_door_rule(attributes={"種別": ["引戸", "片引戸"]})])
    )

    with_kind = _door_quantity(attributes={"種別": "引戸"})
    (mapping,) = map_quantities([with_kind], ruleset).mappings
    assert mapping.status == "unique"

    without_kind = _door_quantity(attributes={})
    (mapping,) = map_quantities([without_kind], ruleset).mappings
    assert mapping.status == "unmapped"
    assert any("種別" in reason for reason in mapping.reasons)


def test_attribute_values_are_compared_after_width_normalisation() -> None:
    """全角と半角の違いだけで当たらなくならないこと。"""
    ruleset = parse_rules(
        _ruleset_payload([_door_rule(attributes={"種別": ["ＳＤ"]})])
    )
    (mapping,) = map_quantities(
        [_door_quantity(attributes={"種別": "SD"})], ruleset
    ).mappings
    assert mapping.status == "unique"


# ---------------------------------------------------------------------------
# 6. 単位
# ---------------------------------------------------------------------------


def test_a_rule_whose_line_unit_has_another_dimension_produces_no_line() -> None:
    """箇所の数量を㎡の行に当てない。勝手に換算しない。"""
    rule = _door_rule()
    rule["line_items"] = [{"work_item": "建具まわり 内装", "unit": "㎡"}]
    ruleset = parse_rules(_ruleset_payload([rule]))

    (mapping,) = map_quantities([_door_quantity()], ruleset).mappings
    assert mapping.status == "unmapped"
    assert any("単位" in reason for reason in mapping.reasons)


def test_the_value_is_expressed_in_the_unit_the_line_declares() -> None:
    """表記だけが違う単位(㎡ と m²)は同じ値として扱う。"""
    rule = {
        "rule_id": "floor",
        "kind": "専有延床面積",
        "line_items": [{"work_item": "床 施工", "unit": "m²"}],
    }
    ruleset = parse_rules(_ruleset_payload([rule]))
    area = QuantityItem(
        target="専有延床面積",
        value_range=(95.54, 95.54),
        unit="㎡",
        method_id="pdf_text_area",
    )
    (mapping,) = map_quantities([area], ruleset).mappings
    (outcome,) = mapping.outcomes
    (line,) = outcome.lines
    assert line.unit == "m²"
    assert line.value_range == (95.54, 95.54)
    assert line.canonical_range == (955400, 955400)


def test_a_quantity_with_an_unknown_unit_is_refused_when_it_is_built() -> None:
    with pytest.raises(QuantityError):
        QuantityItem(
            target="謎::1", value_range=(1.0, 1.0), unit="人工", method_id="x"
        )


# ---------------------------------------------------------------------------
# 5. 同じ行に当たった数量を足さない
# ---------------------------------------------------------------------------


def test_two_quantities_hitting_the_same_line_are_not_summed() -> None:
    """既存平面図と新設平面図の開き戸を足すと、同じ建具を二重に数える。

    入口が「ページをまたいで足さない」と決めているので、ここでも足さず、
    衝突として人へ回す。
    """
    rule = {
        "rule_id": "door-arc",
        "kind": "開き戸",
        "line_items": [{"code": "D-100", "work_item": "建具 取付費", "unit": "箇所"}],
    }
    ruleset = parse_rules(_ruleset_payload([rule]))
    page1 = QuantityItem(
        target="開き戸::ページ1",
        value_range=(2.0, 2.0),
        unit="箇所",
        method_id="pdf_vector_door_arc",
    )
    page2 = QuantityItem(
        target="開き戸::ページ2",
        value_range=(3.0, 3.0),
        unit="箇所",
        method_id="pdf_vector_door_arc",
    )
    result = map_quantities([page1, page2], ruleset)

    values = [
        line.value_range
        for mapping in result.mappings
        for outcome in mapping.outcomes
        for line in outcome.lines
    ]
    assert sorted(values) == [(2.0, 2.0), (3.0, 3.0)]
    assert (5.0, 5.0) not in values

    assert len(result.collisions) == 1
    collision = result.collisions[0]
    assert set(collision.targets) == {"開き戸::ページ1", "開き戸::ページ2"}


def test_a_line_collision_stops_a_line_from_being_settled() -> None:
    """数量が確定していて規則が一意でも、行が衝突していれば確定しない。"""
    rule = {
        "rule_id": "door-arc",
        "kind": "開き戸",
        "line_items": [{"code": "D-100", "work_item": "建具 取付費", "unit": "箇所"}],
    }
    ruleset = parse_rules(_ruleset_payload([rule]))
    quantities = [
        QuantityItem(
            target=f"開き戸::ページ{page}",
            value_range=(float(page), float(page)),
            unit="箇所",
            method_id="pdf_vector_door_arc",
            tier=1,
            action="auto_confirm",
            confirmed_range=(page, page),
        )
        for page in (1, 2)
    ]
    result = map_quantities(quantities, ruleset)
    assert all(mapping.settled is False for mapping in result.mappings)
    assert result.settled_lines() == ()


# ---------------------------------------------------------------------------
# 金額は出さない
# ---------------------------------------------------------------------------


def test_no_price_or_amount_is_produced() -> None:
    """この仕組みは数量を行に当てはめるところまで。単価・金額は出さない。"""
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    (mapping,) = map_quantities([_door_quantity()], ruleset).mappings
    (outcome,) = mapping.outcomes
    for line in outcome.lines:
        fields = line.as_dict()
        assert "unit_price" not in fields
        assert "amount" not in fields


# ---------------------------------------------------------------------------
# 入口からの通し
# ---------------------------------------------------------------------------


@pytest.fixture()
def drawing(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_plan.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    _scanned_page(doc)
    doc.save(path)
    doc.close()
    return path


def test_the_intake_result_becomes_quantities(drawing: Path, tmp_path: Path) -> None:
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing, case_id="MAP-001", answers_path=tmp_path / "answers.json"
        )
    )
    quantities = quantities_from_intake(result)

    assert {item.target for item in quantities} == {item.target for item in result.findings}
    kinds = {item.kind for item in quantities}
    assert "開き戸" in kinds
    assert all(item.tier is not None for item in quantities)


def test_nothing_is_settled_through_the_whole_path(
    drawing: Path, tmp_path: Path
) -> None:
    """**入口から見積の行まで通しても 1 件も確定しない。**

    入口の `test_nothing_is_auto_confirmed_through_this_path` と対になる。
    どちらかの手法が `calibrated=True` になったとき、両方が気づく。
    """
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing, case_id="MAP-002", answers_path=tmp_path / "answers.json"
        )
    )
    quantities = quantities_from_intake(result)
    ruleset = load_rules(EXAMPLE_RULES)
    mapped = map_quantities(quantities, ruleset)

    assert mapped.settled_lines() == ()
    assert all(mapping.settled is False for mapping in mapped.mappings)


def test_the_door_kind_from_the_schedule_becomes_an_attribute() -> None:
    """建具表の種別が、規則が条件に使える属性として渡ること。"""
    rows = (
        DoorScheduleRow(
            mark="AW-1",
            kind="引戸",
            width_text="1650",
            page_number=1,
            cells={"種別": ScheduleCell("引戸", (0, 0, 1, 1), 1)},
        ),
    )
    quantity = QuantityItem(
        target="建具数量::AW-1",
        value_range=(2.0, 2.0),
        unit="箇所",
        method_id="pdf_table_door_schedule",
    )
    from estimating.from_intake import attributes_for_door_mark

    assert attributes_for_door_mark("AW-1", rows) == ({"種別": "引戸", "幅": "1650"}, ())


def test_an_attribute_that_disagrees_across_pages_is_not_set() -> None:
    """同じ建具番号の種別がページで食い違ったら、属性を付けない。

    片方を選ぶと、選ばなかったほうの規則が黙って外れる。
    """
    rows = (
        DoorScheduleRow(mark="AW-1", kind="引戸", page_number=1),
        DoorScheduleRow(mark="AW-1", kind="開き戸", page_number=2),
    )
    from estimating.from_intake import attributes_for_door_mark

    attributes, notes = attributes_for_door_mark("AW-1", rows)
    assert "種別" not in attributes
    assert any("種別" in note for note in notes)


def test_the_summary_says_how_many_were_not_mapped(tmp_path: Path) -> None:
    """報告にそのまま貼れる要約に、当てはまらなかった件数が出ること。"""
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    quantities = [
        _door_quantity("AW-1"),
        QuantityItem(
            target="専有延床面積",
            value_range=(95.54, 95.54),
            unit="㎡",
            method_id="pdf_text_area",
        ),
    ]
    summary = map_quantities(quantities, ruleset).summary()
    assert "確定: 0 件" in summary
    assert "当てはまらなかった数量: 1 件" in summary


def test_rules_can_be_swapped_without_touching_the_code(tmp_path: Path) -> None:
    """同じ数量でも、渡す規則ファイルを変えれば当てはめ先が変わること。"""
    path = tmp_path / "company_rules.json"
    rule = _door_rule("other")
    rule["line_items"] = [{"work_item": "別会社の行", "unit": "箇所"}]
    path.write_text(
        json.dumps(_ruleset_payload([rule]), ensure_ascii=False), encoding="utf-8"
    )

    (mapping,) = map_quantities([_door_quantity()], load_rules(path)).mappings
    (outcome,) = mapping.outcomes
    assert [line.work_item for line in outcome.lines] == ["別会社の行"]


def test_a_line_carries_what_produced_it() -> None:
    """**行 1 つを取り出しただけで、何が効いたかが分かる。**

    いままで規則・手法・軸・階層は木の形でしか無く(`MappingResult` →
    `QuantityMapping` → `RuleOutcome` → `MappedLine`)、
    **行だけを取り出す経路を通ると落ちていた。**報告や見積に出るのはその形である。
    """
    ruleset = parse_rules(_ruleset_payload([_door_rule()]))
    result = map_quantities([_door_quantity()], ruleset)

    (mapping,) = result.mappings
    for line in mapping.lines:
        assert line.rule_id == "door-install"
        assert line.method_id == _door_quantity().method_id
        assert line.axis_id == _door_quantity().axis_id
        assert line.tier == _door_quantity().tier
        assert line.action == _door_quantity().action
        # 行を辞書に畳んでも落ちない。**落ちる経路がこれまでの穴だった。**
        payload = line.as_dict()
        for field_name in ("rule_id", "method_id", "axis_id", "tier", "action"):
            assert field_name in payload, f"{field_name} が as_dict に無い"


# ---------------------------------------------------------------------------
# 8. 規則が現況/計画を条件にできる(版 3、2026-09-23)
# ---------------------------------------------------------------------------
#
# それまで現況/計画は対象名(`開き戸::現況::ページ1`)の中にしか無く、
# `kind` が `::` の手前しか見ないので**規則からは見えなかった**
# (`docs/principles/scope_of_work_diff.md` 3-2)。意味の4欄へ移したので
# 条件にできる。**意味が付いていない数量と、`不明` のままの数量には当てない。**


def _phase_quantity(phase: str | None) -> QuantityItem:
    meaning = (
        Meaning(
            what="建具の数量",
            where="建具表 AW-1 の行",
            phase=phase,
            purpose_link=PURPOSE_UNESTABLISHED,
        )
        if phase is not None
        else None
    )
    return QuantityItem(
        target="建具数量::AW-1",
        value_range=(3.0, 3.0),
        unit="箇所",
        method_id="pdf_table_door_schedule",
        tier=3,
        action="requires_review",
        meaning=meaning,
    )


def test_a_rule_can_require_a_phase() -> None:
    ruleset = parse_rules(_ruleset_payload([_door_rule(phase=["計画"])]))

    (mapping,) = map_quantities([_phase_quantity("計画")], ruleset).mappings
    assert [line.rule_id for line in mapping.lines] == ["door-install"] * 2


def test_a_rule_that_requires_a_phase_misses_the_other_phase() -> None:
    ruleset = parse_rules(_ruleset_payload([_door_rule(phase=["計画"])]))

    (mapping,) = map_quantities([_phase_quantity("現況")], ruleset).mappings
    assert not mapping.lines
    assert any("現況" in reason for reason in mapping.reasons)


def test_a_rule_that_requires_a_phase_misses_a_quantity_without_a_meaning() -> None:
    """**意味が付いていないことを「不明」と読み替えない。**

    読めなかった属性で行を決めない約束(`attributes`)と同じ向きである。
    """
    ruleset = parse_rules(_ruleset_payload([_door_rule(phase=["計画"])]))

    (mapping,) = map_quantities([_phase_quantity(None)], ruleset).mappings
    assert not mapping.lines


def test_a_rule_that_requires_a_phase_misses_an_unknown_phase() -> None:
    """`不明` のままの数量に、現況/計画の規則を当てない。"""
    ruleset = parse_rules(_ruleset_payload([_door_rule(phase=["計画"])]))

    (mapping,) = map_quantities([_phase_quantity("不明")], ruleset).mappings
    assert not mapping.lines


def test_a_rule_cannot_require_an_unknown_phase() -> None:
    """規則の条件に `不明` は書けない。決まっていないことで行を立てない。"""
    with pytest.raises(RuleError):
        parse_rules(_ruleset_payload([_door_rule(phase=["不明"])]))


def test_an_older_ruleset_cannot_use_the_phase_condition() -> None:
    """版を上げずに新しい項目を書いたファイルは断る(約束 7 と同じ)。"""
    payload = _ruleset_payload([_door_rule(phase=["計画"])])
    payload["format_version"] = 2
    with pytest.raises(RuleError):
        parse_rules(payload)
