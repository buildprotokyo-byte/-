"""目的と現況の申告の受け皿(`intake/start_kit.py`)のテスト。

おーちゃんの原則3-2(目的)と3-3(現況)。**画面は作らず、データの受け皿だけ。**

固定したい約束は 7 つ。

1. **目的として人が与えるのは、方向性の自由記述と資料の指定だけ。**
   詳細を設定させない(設定させると自動積算の意味を失う)。
2. **現況として人が申告するのは、把握の状態だけ。** 正確な現況を用意させない。
   混在のときは範囲ごとに申告する。
3. **未申告は「不明」として扱う。** 既定で「分かっている」に倒さない。
4. **どちらも未入力でも止まらない。** 前提を渡さなければ今までどおり動く。
5. **前提を書き換えたら指紋が変わる。** 「どの前提で出した数量か」が辿れなくなる。
6. 目的も現況の申告も、**案件の前提として1つの仕組みに乗る**。別の仕組みを作らない。
7. **未申告は仮説として記録される。** 黙って「不明」で進めない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from estimating.case_premises import SOURCE_HUMAN, SOURCE_HYPOTHESIS
from estimating.from_intake import premises_from_intake
from intake.drawing_intake import IntakeConfig, read_drawing, start_kit_fingerprint
from intake.start_kit import (
    AWARENESS_MIXED,
    AWARENESS_UNKNOWN,
    CONDITION_AWARENESS,
    ConditionRange,
    ConditionSurvey,
    Purpose,
    SourceDocument,
    StartKit,
    StartKitError,
)

import pymupdf

from tests.test_drawing_intake import _vector_plan_page


# ---------------------------------------------------------------------------
# 1. 目的は方向性と資料の指定だけ
# ---------------------------------------------------------------------------


def test_the_purpose_is_free_text_and_a_pointer_to_documents() -> None:
    purpose = Purpose(
        direction="戸建ての水回りリフォーム",
        source_documents=(
            SourceDocument(label="基本仕様書"),
            SourceDocument(label="イメージパース", page_number=4),
        ),
    )
    assert purpose.direction == "戸建ての水回りリフォーム"
    assert [d.label for d in purpose.source_documents] == ["基本仕様書", "イメージパース"]


def test_a_purpose_with_neither_a_direction_nor_a_document_is_refused() -> None:
    """**空の目的を受け取らない。** 与えていないことと、空を与えたことは別。"""
    with pytest.raises(StartKitError):
        Purpose(direction="   ")


def test_a_purpose_may_be_documents_only() -> None:
    purpose = Purpose(source_documents=(SourceDocument(label="要望書"),))
    assert purpose.direction == ""


def test_a_source_document_needs_a_label() -> None:
    with pytest.raises(StartKitError):
        SourceDocument(label="  ")


def test_a_source_document_page_must_be_one_based() -> None:
    with pytest.raises(StartKitError):
        SourceDocument(label="基本仕様書", page_number=0)


def test_the_direction_is_only_a_weak_hint() -> None:
    """方向性は弱い手がかりとしてだけ扱う、と読める形で持っていること。"""
    assert Purpose(direction="水回り").strength == "weak"


# ---------------------------------------------------------------------------
# 2. 現況は把握の状態の申告だけ
# ---------------------------------------------------------------------------


def test_the_four_awareness_options_are_the_ones_the_principle_names() -> None:
    assert CONDITION_AWARENESS == (
        "明確に分かる",
        "おおむね分かるが確実ではない",
        "全く分からない、または図面で表現されていない",
        "部分によって混在する",
    )


def test_a_clear_survey_needs_no_ranges() -> None:
    survey = ConditionSurvey(overall="明確に分かる")
    assert survey.ranges == ()
    assert survey.awareness_overall == "明確に分かる"


def test_a_mixed_survey_must_say_which_range_is_which() -> None:
    """「部分によって混在する」なら**範囲ごとに申告する。**"""
    with pytest.raises(StartKitError, match="範囲"):
        ConditionSurvey(overall=AWARENESS_MIXED)


def test_a_mixed_survey_accepts_ranges() -> None:
    survey = ConditionSurvey(
        overall=AWARENESS_MIXED,
        ranges=(
            ConditionRange(description="1階の水回り", awareness="明確に分かる"),
            ConditionRange(description="2階", awareness=AWARENESS_UNKNOWN),
        ),
    )
    assert len(survey.ranges) == 2


def test_a_range_may_not_itself_be_mixed() -> None:
    """範囲の中でまた「混在」と言われると、いつまでも決まらない。"""
    with pytest.raises(StartKitError, match="混在"):
        ConditionSurvey(
            overall=AWARENESS_MIXED,
            ranges=(ConditionRange(description="1階", awareness=AWARENESS_MIXED),),
        )


def test_ranges_are_refused_when_the_survey_is_not_mixed() -> None:
    with pytest.raises(StartKitError):
        ConditionSurvey(
            overall="明確に分かる",
            ranges=(ConditionRange(description="1階", awareness="明確に分かる"),),
        )


def test_an_unknown_awareness_is_refused() -> None:
    with pytest.raises(StartKitError):
        ConditionSurvey(overall="だいたい把握している")


def test_a_range_needs_a_description() -> None:
    with pytest.raises(StartKitError):
        ConditionRange(description="  ", awareness="明確に分かる")


# ---------------------------------------------------------------------------
# 3. 未申告は「不明」として扱う
# ---------------------------------------------------------------------------


def test_no_survey_at_all_means_unknown_not_clear() -> None:
    """**既定で「分かっている」に倒さない。**"""
    kit = StartKit()
    assert kit.condition_survey is None
    assert kit.effective_condition_awareness() == AWARENESS_UNKNOWN


def test_a_declared_survey_is_used_as_declared() -> None:
    kit = StartKit(condition_survey=ConditionSurvey(overall="明確に分かる"))
    assert kit.effective_condition_awareness() == "明確に分かる"


# ---------------------------------------------------------------------------
# 4. 未入力でも止まらない
# ---------------------------------------------------------------------------


def test_the_start_kit_is_still_empty_without_a_purpose_or_a_survey() -> None:
    assert StartKit().is_empty
    assert not StartKit(purpose=Purpose(direction="水回り")).is_empty
    assert not StartKit(condition_survey=ConditionSurvey(overall="明確に分かる")).is_empty


def test_a_drawing_still_reads_with_a_purpose_and_a_survey(tmp_path: Path) -> None:
    """前提を足しても、入口はそのまま動く。**止める関門にしない。**"""
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    _vector_plan_page(doc)
    doc.save(pdf)
    doc.close()
    result = read_drawing(
        IntakeConfig(
            pdf_path=pdf,
            case_id="TEST-PURPOSE",
            start_kit=StartKit(
                purpose=Purpose(direction="戸建ての水回りリフォーム"),
                condition_survey=ConditionSurvey(overall="おおむね分かるが確実ではない"),
                entered_by="おーちゃん",
            ),
        )
    )
    assert result.pages
    assert result.purpose is not None
    assert result.condition_survey is not None
    # **目的も現況の申告も、何かを確定させはしない。**
    assert result.confirmed_targets == ()


# ---------------------------------------------------------------------------
# 5. 前提を書き換えたら指紋が変わる
# ---------------------------------------------------------------------------


def test_the_fingerprint_changes_when_the_purpose_changes() -> None:
    a = start_kit_fingerprint(StartKit(purpose=Purpose(direction="水回り")))
    b = start_kit_fingerprint(StartKit(purpose=Purpose(direction="外構")))
    assert a != b, "目的を変えても指紋が同じでは、どの前提で出した数量か辿れない"


def test_the_fingerprint_changes_when_the_survey_changes() -> None:
    a = start_kit_fingerprint(
        StartKit(condition_survey=ConditionSurvey(overall="明確に分かる"))
    )
    b = start_kit_fingerprint(
        StartKit(condition_survey=ConditionSurvey(overall=AWARENESS_UNKNOWN))
    )
    assert a != b


def test_the_fingerprint_changes_when_a_source_document_changes() -> None:
    a = start_kit_fingerprint(
        StartKit(purpose=Purpose(source_documents=(SourceDocument(label="要望書"),)))
    )
    b = start_kit_fingerprint(
        StartKit(purpose=Purpose(source_documents=(SourceDocument(label="基本仕様書"),)))
    )
    assert a != b


def test_an_empty_start_kit_still_has_a_stable_fingerprint() -> None:
    assert start_kit_fingerprint(StartKit()) == start_kit_fingerprint(StartKit())


# ---------------------------------------------------------------------------
# 6〜7. 案件の前提として、1つの仕組みに乗る
# ---------------------------------------------------------------------------


class _Result:
    """`IntakeResult` の形だけをまねた合成の結果。"""

    def __init__(self, purpose=None, condition_survey=None, entered_by="") -> None:
        self.purpose = purpose
        self.condition_survey = condition_survey
        self.start_kit_entered_by = entered_by


def test_a_declared_purpose_becomes_a_human_premise() -> None:
    premises = premises_from_intake(
        _Result(purpose=Purpose(direction="戸建ての水回りリフォーム"), entered_by="おーちゃん")
    )
    purpose = [p for p in premises if p.kind == "目的の方向性"]
    assert len(purpose) == 1
    assert purpose[0].source == SOURCE_HUMAN
    assert purpose[0].entered_by == "おーちゃん"
    assert "水回り" in purpose[0].statement


def test_a_declared_survey_becomes_a_human_premise() -> None:
    premises = premises_from_intake(
        _Result(
            condition_survey=ConditionSurvey(overall="明確に分かる"), entered_by="おーちゃん"
        )
    )
    survey = [p for p in premises if p.kind == "現況の把握の状態"]
    assert len(survey) == 1
    assert survey[0].source == SOURCE_HUMAN


def test_an_undeclared_survey_becomes_a_hypothesis_not_silence() -> None:
    """**黙って「不明」で進めない。** 仮説として記録し、人に見える形にする。"""
    premises = premises_from_intake(_Result())
    survey = [p for p in premises if p.kind == "現況の把握の状態"]
    assert len(survey) == 1
    assert survey[0].source == SOURCE_HYPOTHESIS
    assert survey[0].resolved_by, "何が分かればこの仮説が要らなくなるかが書かれていない"
    assert AWARENESS_UNKNOWN in survey[0].statement


def test_an_undeclared_purpose_produces_no_premise_at_all() -> None:
    """**目的は無いなら無い。** 「目的が不明」という前提をでっち上げない。"""
    premises = premises_from_intake(_Result())
    assert [p for p in premises if p.kind == "目的の方向性"] == []


def test_the_source_documents_are_recorded_in_the_premise() -> None:
    premises = premises_from_intake(
        _Result(
            purpose=Purpose(
                direction="水回り",
                source_documents=(SourceDocument(label="基本仕様書", page_number=2),),
            ),
            entered_by="おーちゃん",
        )
    )
    (purpose,) = [p for p in premises if p.kind == "目的の方向性"]
    assert "基本仕様書" in purpose.statement


def test_the_premises_have_distinct_ids() -> None:
    premises = premises_from_intake(
        _Result(
            purpose=Purpose(direction="水回り"),
            condition_survey=ConditionSurvey(overall="明確に分かる"),
            entered_by="おーちゃん",
        )
    )
    ids = [p.premise_id for p in premises]
    assert len(ids) == len(set(ids))
