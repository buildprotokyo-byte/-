"""**行 1 つを見て「何が決め手だったか」が分かること。**

おーちゃんの指示(優先順1番)。基準は `docs/d_decisive_reason_criteria.md`。

固定したい約束。

1. **証拠が無ければ決め手を名乗れない。** ルールIDの無い「知識のルール」、
   経路が 1 本だけの「経路の一致」、問いの分からない「人の回答」は作れない。
2. **種類に合わない証拠を付けられない。** 「観測だけ」にルールIDは付かない。
3. **本番経路を通した行に決め手が付く。** 行だけを取り出す経路
   (`as_dict()`)でも落ちない(64周目に踏んだ落とし穴)。
4. **当たった行・外した行を理由別に集計できる。** 既存の指標は残す。
5. **取れなかったものも理由別に数えられる。**「その他」は説明が無ければ作れない。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from estimating.decisive import (
    NOT_OBTAINED_AREA,
    NOT_OBTAINED_NO_RULE,
    NOT_OBTAINED_OTHER,
    NOT_OBTAINED_SITE_SURVEY,
    NOT_OBTAINED_SYMBOL,
    REASON_HUMAN_ANSWER,
    REASON_KNOWLEDGE_RULE,
    REASON_OBSERVED,
    REASON_PATHS_AGREED,
    REASON_SUMMARY_SEARCH,
    DecisiveError,
    DecisiveReason,
    NotObtained,
    counts_by_reason,
    decisive_reasons_for,
    lines_without_reason,
    not_obtained_counts,
    score_by_reason,
)
from estimating.from_intake import quantities_from_intake
from estimating.mapping import MappedLine, map_quantities
from estimating.quantities import QuantityItem
from estimating.rules import load_rules
from intake.drawing_intake import IntakeConfig, read_drawing

from tests.test_drawing_intake import _scanned_page, _vector_plan_page

EXAMPLE_RULES = (
    Path(__file__).resolve().parents[1] / "estimating" / "examples" / "synthetic_rules.json"
)


# ---------------------------------------------------------------------------
# 1. 証拠が無ければ名乗れない
# ---------------------------------------------------------------------------


def test_a_knowledge_rule_without_a_rule_id_is_refused() -> None:
    with pytest.raises(DecisiveError):
        DecisiveReason(kind=REASON_KNOWLEDGE_RULE)


def test_agreement_needs_two_paths_and_a_word_on_independence() -> None:
    with pytest.raises(DecisiveError):
        DecisiveReason(
            kind=REASON_PATHS_AGREED,
            agreeing_paths=("pdf_text_area",),
            paths_independent=True,
        )
    with pytest.raises(DecisiveError):
        # **独立しているかどうかを書かないまま「一致した」と言わせない。**
        DecisiveReason(
            kind=REASON_PATHS_AGREED,
            agreeing_paths=("pdf_text_area", "pdf_vector_door_arc"),
        )


def test_a_human_answer_without_a_question_is_refused() -> None:
    with pytest.raises(DecisiveError):
        DecisiveReason(kind=REASON_HUMAN_ANSWER)


def test_a_summary_search_without_an_item_is_refused() -> None:
    with pytest.raises(DecisiveError):
        DecisiveReason(kind=REASON_SUMMARY_SEARCH)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(DecisiveError):
        DecisiveReason(kind="なんとなく")


def test_evidence_of_another_kind_is_refused() -> None:
    """**「観測だけ」にルールIDは付かない。**"""
    with pytest.raises(DecisiveError):
        DecisiveReason(kind=REASON_OBSERVED, knowledge_rule_ids=("rule-1",))


# ---------------------------------------------------------------------------
# 2. 決め手の決め方
# ---------------------------------------------------------------------------


def test_reading_the_drawing_plainly_is_observation() -> None:
    reasons = decisive_reasons_for(effective_derivation="read", source_kind="drawing")

    assert [r.kind for r in reasons] == [REASON_OBSERVED]


def test_observation_is_only_claimed_when_nothing_else_applies() -> None:
    """**「観測だけで出た」の「だけ」を守る。**"""
    reasons = decisive_reasons_for(
        effective_derivation="read",
        source_kind="drawing",
        knowledge_rule_ids=("kabe-shitaji",),
    )

    assert [r.kind for r in reasons] == [REASON_KNOWLEDGE_RULE]


def test_a_value_filled_in_by_a_general_rule_gets_no_reason() -> None:
    """**一般則で補った値は、ルールIDが無いかぎり決め手を名乗れない。**

    ここを「観測」にすると、読んだ値と補った値が行の上で見分けられなくなる。
    **空欄のまま残し、集計で名指しする。**
    """
    assert decisive_reasons_for(effective_derivation="assumed", source_kind="drawing") == ()


def test_agreeing_paths_become_a_reason() -> None:
    reasons = decisive_reasons_for(
        effective_derivation="read",
        source_kind="drawing",
        agreeing_paths=("pdf_text_area", "pdf_vector_door_arc"),
        paths_independent=False,
    )

    assert [r.kind for r in reasons] == [REASON_PATHS_AGREED]
    assert reasons[0].paths_independent is False


def test_the_same_path_twice_is_not_an_agreement() -> None:
    """**同じ経路を 2 回数えて「一致した」にしない。**"""
    reasons = decisive_reasons_for(
        effective_derivation="read",
        source_kind="drawing",
        agreeing_paths=("pdf_text_area", "pdf_text_area"),
        paths_independent=True,
    )

    assert [r.kind for r in reasons] == [REASON_OBSERVED]


# ---------------------------------------------------------------------------
# 3. 本番経路を通した行に付く
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


def test_a_quantity_keeps_the_evidence_not_the_label() -> None:
    """**札ではなく証拠を持つ。** 証拠の無い札を手で付けられないようにする。"""
    quantity = QuantityItem(
        target="基準寸法::ページ1",
        value_range=(4000.0, 4000.0),
        unit="mm",
        method_id="human_reference_point",
        source_kind="start_kit",
        question_id="start_kit::human_reference_point",
    )

    assert [r.kind for r in quantity.decisive] == [REASON_HUMAN_ANSWER]
    assert quantity.decisive[0].question_id == "start_kit::human_reference_point"


def test_lines_from_the_production_path_carry_a_decisive_reason(
    drawing: Path, tmp_path: Path
) -> None:
    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing, case_id="DEC-001", answers_path=tmp_path / "answers.json"
        )
    )
    quantities = quantities_from_intake(result)
    mapping = map_quantities(quantities, load_rules(EXAMPLE_RULES))

    lines = [line for m in mapping.mappings for line in m.lines]
    assert lines, "行が 1 行も出ていない(この対照が通らないなら測らない)"
    assert all(line.decisive for line in lines)
    # **行だけを取り出す経路でも落ちない。**
    assert all(row["decisive"] for row in (line.as_dict() for line in lines))


# ---------------------------------------------------------------------------
# 4. 理由別の集計
# ---------------------------------------------------------------------------


def _line(reason_kinds: tuple[str, ...]) -> MappedLine:
    """決め手だけを持たせた行。**集計を測るための最小の行。**"""
    return MappedLine(
        work_item="建具工事",
        unit="箇所",
        value_range=(1.0, 1.0),
        canonical_range=(1, 1),
        decisive=tuple(
            DecisiveReason(
                kind=kind,
                question_id="q1" if kind == REASON_HUMAN_ANSWER else "",
            )
            for kind in reason_kinds
        ),
    )


def test_counts_are_kept_per_reason() -> None:
    lines = [_line((REASON_OBSERVED,)), _line((REASON_HUMAN_ANSWER,)), _line((REASON_OBSERVED,))]

    assert counts_by_reason(lines) == {REASON_OBSERVED: 2, REASON_HUMAN_ANSWER: 1}


def test_hits_and_misses_are_scored_per_reason() -> None:
    hits = [_line((REASON_OBSERVED,)), _line((REASON_HUMAN_ANSWER,))]
    misses = [_line((REASON_OBSERVED,))]

    scores = {s.reason: s for s in score_by_reason(hits, misses)}

    assert (scores[REASON_OBSERVED].hit, scores[REASON_OBSERVED].miss) == (1, 1)
    assert scores[REASON_OBSERVED].hit_rate == pytest.approx(0.5)
    assert scores[REASON_HUMAN_ANSWER].hit_rate == pytest.approx(1.0)


def test_lines_without_a_reason_are_named_not_hidden() -> None:
    lines = [_line(()), _line((REASON_OBSERVED,))]

    assert len(lines_without_reason(lines)) == 1


# ---------------------------------------------------------------------------
# 5. 取れなかったもの
# ---------------------------------------------------------------------------


def test_not_obtained_is_counted_per_reason() -> None:
    items = [
        NotObtained(reason=NOT_OBTAINED_SYMBOL, target="引戸::2階"),
        NotObtained(reason=NOT_OBTAINED_AREA, target="施工対象床面積::全体"),
        NotObtained(reason=NOT_OBTAINED_NO_RULE, target="開き戸::1階"),
        NotObtained(reason=NOT_OBTAINED_SITE_SURVEY, target="下地::1階"),
    ]

    assert not_obtained_counts(items) == {
        NOT_OBTAINED_SYMBOL: 1,
        NOT_OBTAINED_AREA: 1,
        NOT_OBTAINED_NO_RULE: 1,
        NOT_OBTAINED_SITE_SURVEY: 1,
    }


def test_other_needs_a_reason_in_words() -> None:
    """**「その他」で束ねて中身を消さない。**"""
    with pytest.raises(DecisiveError):
        NotObtained(reason=NOT_OBTAINED_OTHER, target="不明")

    assert NotObtained(
        reason=NOT_OBTAINED_OTHER, target="不明", detail="単位が解釈できない"
    ).reason == NOT_OBTAINED_OTHER


def test_an_unknown_not_obtained_reason_is_refused() -> None:
    with pytest.raises(DecisiveError):
        NotObtained(reason="なんとなく取れない", target="開き戸::1階")


def test_a_quantity_with_no_rule_is_counted_as_no_rule() -> None:
    quantity = QuantityItem(
        target="知らない種類::1階",
        value_range=(1.0, 1.0),
        unit="箇所",
        method_id="pdf_vector_door_arc",
    )
    result = map_quantities([quantity], load_rules(EXAMPLE_RULES))

    assert not_obtained_counts(result.not_obtained()) == {NOT_OBTAINED_NO_RULE: 1}


def test_a_quantity_that_needs_a_site_survey_is_counted_as_such() -> None:
    quantity = QuantityItem(
        target="開き戸::1階",
        value_range=(3.0, 3.0),
        unit="箇所",
        method_id="pdf_vector_door_arc",
        site_survey_reason="天井裏の下地は図面に書かれていない",
    )
    result = map_quantities([quantity], load_rules(EXAMPLE_RULES))

    assert not_obtained_counts(result.not_obtained()) == {NOT_OBTAINED_SITE_SURVEY: 1}


def test_a_scanned_page_is_counted_as_symbols_unreadable(
    drawing: Path, tmp_path: Path
) -> None:
    """**スキャンのページからは記号を 1 つも数えられない。**理由として残す。"""
    from estimating.from_intake import not_obtained_from_intake

    result = read_drawing(
        IntakeConfig(
            pdf_path=drawing, case_id="DEC-002", answers_path=tmp_path / "answers.json"
        )
    )

    counts = not_obtained_counts(not_obtained_from_intake(result))
    assert counts.get(NOT_OBTAINED_SYMBOL, 0) >= 1


def test_a_value_calculated_from_read_values_is_still_observation() -> None:
    """**読んだ値から計算した値も観測。** 図面の外の根拠が入っていない。

    ただし**計算したことを書き添える**ので、素直に読めた値と見分けられる。
    (本番経路の `pdf_text_scale` がこれで、空欄のままだと
    「なぜこの行が出たか分からない行」に見えてしまう。)
    """
    reasons = decisive_reasons_for(
        effective_derivation="derived", source_kind="drawing"
    )

    assert [r.kind for r in reasons] == [REASON_OBSERVED]
    assert reasons[0].detail == "読んだ値から計算した"
