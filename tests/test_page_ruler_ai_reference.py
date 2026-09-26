"""AI が選んだ基準の寸法線から目盛りを作り、ほかの寸法で検算する(K-37 やること 2)。

**合成データだけ**を使う。基準は `docs/k37_dimensions_to_quantities_criteria.md`(測る前にコミット済み)。

守りたいこと
1. 出どころは「AI が選んだ基準の寸法」で、**人が入れた基準の長さと混ぜない。**
2. 誰が・なぜ選んだかを残す。理由の無い選択は受け付けない。
3. 検算: ほかの寸法が全部そろえば「揃っている」、1 本だけ外れればその寸法を疑い、
   2 本以上外れれば基準のほうを疑う。**外れた寸法を捨てず、ずれと一緒に返す。**
4. 表記の縮尺との差は数字で返す(表記を当てにはしないが、比べる相手として残す)。
5. 校正済みにはならない。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from axes.image_axis.page_ruler import (
    SOURCE_AI_REFERENCE,
    SOURCE_HUMAN,
    CHECK_ALL_AGREE,
    CHECK_ONE_OFF,
    CHECK_SEVERAL_OFF,
    RulerError,
    cross_check,
    reconcile,
    ruler_from_chosen_dimension,
    ruler_from_reference_length,
)

MM_PER_PT_AT_50 = 50 * 25.4 / 72


@dataclass(frozen=True)
class _Dim:
    text: str
    value_mm: float
    start_pt: tuple[float, float]
    end_pt: tuple[float, float]


def _dim(text: str, value_mm: float, x0: float, length_mm: float, *, scale: float = MM_PER_PT_AT_50) -> _Dim:
    return _Dim(text, value_mm, (x0, 100.0), (x0 + length_mm / scale, 100.0))


def _ruler(reference: _Dim):
    return ruler_from_chosen_dimension(
        0,
        reference.start_pt,
        reference.end_pt,
        reference.value_mm,
        text=reference.text,
        chosen_by="AI(図面の画像と寸法の一覧だけを見た読み手)",
        reason="外周の連なりの合計と一致する最も長い寸法",
    )


def test_the_ai_choice_is_its_own_source_and_keeps_why() -> None:
    ruler = _ruler(_dim("8,245", 8245.0, 50.0, 8245.0))

    assert ruler.source == SOURCE_AI_REFERENCE
    assert ruler.source != SOURCE_HUMAN
    assert ruler.mm_per_point == pytest.approx(MM_PER_PT_AT_50)
    assert "8,245" in ruler.note and "外周の連なり" in ruler.note and "AI" in ruler.note
    assert ruler.calibrated is False


def test_a_choice_without_a_reason_is_refused() -> None:
    reference = _dim("8,245", 8245.0, 50.0, 8245.0)
    with pytest.raises(RulerError):
        ruler_from_chosen_dimension(
            0, reference.start_pt, reference.end_pt, reference.value_mm,
            text=reference.text, chosen_by="AI", reason="",
        )


def test_every_other_dimension_agreeing_is_reported_as_agreeing() -> None:
    reference = _dim("8,245", 8245.0, 50.0, 8245.0)
    others = [_dim("400", 400.0, 10.0, 400.0), _dim("5,515", 5515.0, 10.0, 5515.0)]

    check = cross_check(_ruler(reference), others, tolerance=0.01)

    assert check.verdict == CHECK_ALL_AGREE
    assert check.off == ()
    assert check.agreeing == 2


def test_one_dimension_off_is_the_suspect_not_the_reference() -> None:
    reference = _dim("8,245", 8245.0, 50.0, 8245.0)
    wrong_span = _dim("1,825", 1825.0, 10.0, 1825.0 / 1.105)  # 紙の上の区間が短く取られた
    others = [_dim("400", 400.0, 10.0, 400.0), wrong_span]

    check = cross_check(_ruler(reference), others, tolerance=0.01)

    assert check.verdict == CHECK_ONE_OFF
    assert [item.text for item in check.off] == ["1,825"]
    assert check.off[0].deviation == pytest.approx(0.105, abs=1e-3)


def test_several_off_puts_the_reference_in_doubt_and_drops_nothing() -> None:
    reference = _dim("2,000", 2000.0, 50.0, 2000.0 / 1.5)  # 基準のほうが誤っている
    others = [_dim("400", 400.0, 10.0, 400.0), _dim("600", 600.0, 10.0, 600.0)]

    check = cross_check(_ruler(reference), others, tolerance=0.01)

    assert check.verdict == CHECK_SEVERAL_OFF
    assert len(check.off) == 2
    assert check.agreeing + len(check.off) == len(others)


def test_the_printed_scale_is_compared_not_trusted() -> None:
    reference = _dim("8,245", 8245.0, 50.0, 8245.0, scale=MM_PER_PT_AT_50 * 1.02)  # 印刷で 2% 縮んだ紙

    check = cross_check(_ruler(reference), [], tolerance=0.01, printed_denominator=50.0)

    assert check.printed_difference == pytest.approx(0.02, abs=1e-6)
    assert check.verdict == CHECK_ALL_AGREE  # ほかに寸法が無いので外れも無い


def test_reconcile_never_mixes_the_ai_choice_with_a_person() -> None:
    ai = _ruler(_dim("8,245", 8245.0, 50.0, 8245.0))
    person = ruler_from_reference_length(0, (0.0, 0.0), (100.0, 0.0), 100.0 * MM_PER_PT_AT_50 * 1.2)

    assert not isinstance(reconcile([ai, person], tolerance=0.05), type(ai))
