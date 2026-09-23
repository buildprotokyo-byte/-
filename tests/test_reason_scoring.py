"""**出した行を正解に突き合わせ、当たり外れを理由別に数える。**

基準は `docs/d_reason_scoring_criteria.md`。

固定したい約束。

1. **抽出と採点を分ける。** 突き合わせる関数は行と正解だけを受け取る。
2. **対応づけを緩めない。** 工事内容が 1 文字違えば当たりにしない。
   単位が違っても当たりにしない。**似ている語で寄せない。**
3. **正解に `code` があれば `code` で突き合わせる。**
4. **正解にあるのに出せなかった項目を数える。**
5. **出したのに正解に無い行も数える。**「外した行」はこれである。
6. **数量の値は見ない。** この採点が言うのは「その行が出せたか」だけ。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estimating.decisive import REASON_HUMAN_ANSWER, REASON_OBSERVED, DecisiveReason
from estimating.mapping import MappedLine
from estimating.scoring import GoldenItem, load_golden_items, score_lines


def _line(work_item: str, unit: str, *, code: str | None = None, reason: str = REASON_OBSERVED) -> MappedLine:
    return MappedLine(
        work_item=work_item,
        unit=unit,
        value_range=(1.0, 1.0),
        canonical_range=(1, 1),
        code=code,
        decisive=(
            DecisiveReason(
                kind=reason, question_id="q1" if reason == REASON_HUMAN_ANSWER else ""
            ),
        )
        if reason
        else (),
    )


def test_a_line_that_matches_the_golden_is_a_hit() -> None:
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)

    result = score_lines([_line("建具取付", "箇所")], golden)

    assert len(result.hit_lines) == 1
    assert result.missed_items == ()
    assert result.coverage == pytest.approx(1.0)


def test_one_character_of_difference_is_not_a_hit() -> None:
    """**対応づけを緩めない。**"""
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)

    result = score_lines([_line("建具取付け", "箇所")], golden)

    assert result.hit_lines == ()
    assert len(result.missed_items) == 1
    assert len(result.extra_lines) == 1


def test_a_different_unit_is_not_a_hit() -> None:
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)

    result = score_lines([_line("建具取付", "㎡")], golden)

    assert result.hit_lines == ()


def test_the_same_unit_written_differently_is_still_a_hit() -> None:
    """**表記のゆれは吸収する。**(㎡ と m²。意味は変えない)"""
    golden = (GoldenItem(work_item="床仕上", unit="㎡"),)

    result = score_lines([_line("床仕上", "m²")], golden)

    assert len(result.hit_lines) == 1


def test_a_code_wins_over_the_name() -> None:
    golden = (GoldenItem(work_item="正解側の呼び名", unit="箇所", code="A-01"),)

    result = score_lines([_line("こちらの呼び名", "箇所", code="A-01")], golden)

    assert len(result.hit_lines) == 1


def test_an_item_with_no_line_is_counted_as_missed() -> None:
    golden = (
        GoldenItem(work_item="建具取付", unit="箇所"),
        GoldenItem(work_item="床仕上", unit="㎡"),
    )

    result = score_lines([_line("建具取付", "箇所")], golden)

    assert [item.work_item for item in result.missed_items] == ["床仕上"]
    assert result.coverage == pytest.approx(0.5)


def test_hits_and_extras_are_counted_per_reason() -> None:
    """**当たった行と外した行を、決め手の理由別に数える。**"""
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)
    lines = [
        _line("建具取付", "箇所", reason=REASON_OBSERVED),
        _line("よその行", "箇所", reason=REASON_HUMAN_ANSWER),
    ]

    scores = {s.reason: s for s in score_lines(lines, golden).by_reason()}

    assert (scores[REASON_OBSERVED].hit, scores[REASON_OBSERVED].miss) == (1, 0)
    assert (scores[REASON_HUMAN_ANSWER].hit, scores[REASON_HUMAN_ANSWER].miss) == (0, 1)


def test_nothing_is_counted_twice() -> None:
    """**理由別の合計 + 決め手の無い行 = 行の総数。**"""
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)
    lines = [_line("建具取付", "箇所"), _line("よその行", "箇所", reason="")]

    result = score_lines(lines, golden)
    counted = sum(s.total for s in result.by_reason())

    assert counted + len(result.lines_without_reason()) == len(lines)


def test_the_value_of_the_quantity_is_not_looked_at() -> None:
    """**数量の値は見ない。** 値が違っても「行は出せた」と数える。"""
    golden = (GoldenItem(work_item="建具取付", unit="箇所"),)
    line = MappedLine(
        work_item="建具取付",
        unit="箇所",
        value_range=(999.0, 999.0),
        canonical_range=(999, 999),
        decisive=(DecisiveReason(kind=REASON_OBSERVED),),
    )

    assert len(score_lines([line], golden).hit_lines) == 1


def test_the_golden_file_is_read_without_touching_quantities(tmp_path: Path) -> None:
    """**正解ファイルから読むのは項目の名前・単位・符号だけ。**

    数量も単価もこの層に入れない(ゴールデンの運用規則)。
    """
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            {
                "expected_items": [
                    {
                        "code": "A-01",
                        "work_item": "建具取付",
                        "unit": "箇所",
                        "major_category": "建具",
                        "quantity": 3,
                        "unit_price": 12000,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    items = load_golden_items(path)

    assert items == (
        GoldenItem(
            work_item="建具取付", unit="箇所", code="A-01", major_category="建具"
        ),
    )
    assert not hasattr(items[0], "quantity")
