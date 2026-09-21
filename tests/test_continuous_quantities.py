"""連続量(長さ・面積・金額)を固定小数点の整数で扱えることの回帰テスト。

`docs/decision_continuous_quantity_gap.md` 選択肢B(2026-09-21 おーちゃんの
判断により採用)の実装を縛る。

**修正前の実測(commit 849b05f)**::

    unit=m           → unknown_unit
    unit=㎡           → unknown_unit
    unit=円           → unknown_unit
    count_range=[12.5, 13.0] → count_must_be_integer

つまり v8 9.5節が「有効性を確認した」とする連続量は、実運用の入口を
1件も通れなかった。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from arbitration import units
from arbitration.consistency_solver import ConsistencySolver
from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy
from killer_question.engine import _MAX_CANDIDATE_ENUMERATION, KillerQuestionEngine
from killer_question.precision_mode import PrecisionMode


# =====================================================================
# 単位の正規化そのもの
# =====================================================================


@pytest.mark.parametrize(
    ("raw", "value", "expected_unit", "expected"),
    [
        ("m", 12.5, "mm", 12_500),
        ("メートル", 1, "mm", 1_000),
        ("cm", 250, "mm", 2_500),
        ("mm", 12_500, "mm", 12_500),
        ("㎡", 100, "cm2", 1_000_000),
        ("平方メートル", 0.5, "cm2", 5_000),
        ("m2", 1, "cm2", 10_000),
        ("円", 1_500_000, "yen", 1_500_000),
        ("万円", 150, "yen", 1_500_000),
        ("千円", 31.7, "yen", 31_700),
        ("個", 4, "count", 4),
        ("箇所", 7, "count", 7),
    ],
)
def test_values_normalise_to_the_canonical_unit(
    raw: str, value: object, expected_unit: str, expected: int
) -> None:
    assert units.canonical_unit(raw) == expected_unit
    assert units.normalise_value(raw, value) == expected


def test_float_scaling_does_not_leak_binary_error() -> None:
    """float の倍率計算では整数にならない値が、正しく整数になること。

    実測した壊れる例(0.01 きざみの値を 1〜20 の範囲で走査した結果、
    ``m``→``mm`` で34件、``m2``→``cm2`` で228件)::

        2.01 * 1000  = 2009.9999999999998
        0.07 * 10000 = 700.0000000000001

    float のまま掛けると「刻み単位の整数」という約束が崩れ、
    ``value_finer_than_unit_step`` で正当な入力を誤って拒否してしまう。
    Decimal を経由していることの証拠として、この2値を直接縛る。
    """
    # 前提: float では整数にならない
    assert 2.01 * 1000 != 2010
    assert 0.07 * 10000 != 700

    assert units.normalise_value("m", 2.01) == 2_010
    assert units.normalise_value("㎡", 0.07) == 700
    assert units.normalise_value("m", 0.1) == 100
    assert units.normalise_value("m", 4.03) == 4_030


def test_a_value_finer_than_the_step_is_refused_not_rounded() -> None:
    """刻みより細かい値は、黙って丸めずに拒否すること。

    丸めてよいかは数量の種類ごとの判断になるため、単位の層で決めない。
    """
    with pytest.raises(units.UnitError) as caught:
        units.normalise_value("mm", 12.5)
    assert caught.value.code == "value_finer_than_unit_step"

    with pytest.raises(units.UnitError):
        units.normalise_value("個", 4.5)


def test_strings_are_not_accepted_as_quantities() -> None:
    """外部入力の境界なので、"5" を黙って 5 と解釈しないこと。"""
    with pytest.raises(units.UnitError) as caught:
        units.normalise_value("m", "5")
    assert caught.value.code == "invalid_quantity"


def test_unknown_units_are_refused() -> None:
    with pytest.raises(units.UnitError) as caught:
        units.canonical_unit("尺")
    assert caught.value.code == "unknown_unit"


def test_decimal_input_is_accepted() -> None:
    assert units.normalise_value("m", Decimal("12.5")) == 12_500


def test_the_area_limit_admits_a_realistic_building() -> None:
    """床面積 100㎡ が上限に当たらないこと。

    `docs/decision_continuous_quantity_gap.md` 2節で「100㎡ = 1,000,000cm² は
    従来の一律上限 MAX_COUNT ちょうど」と指摘した点。単位ごとの上限にして
    解消したことを縛る。
    """
    _, rng = units.normalise_range("㎡", 100, 100)
    assert rng == (1_000_000, 1_000_000)
    assert units.UNIT_SPECS["cm2"].max_value > 1_000_000

    with pytest.raises(units.UnitError) as caught:
        units.normalise_range("㎡", 200_000, 200_000)  # 20ha
    assert caught.value.code == "quantity_too_large"


def test_count_keeps_its_original_limit() -> None:
    """個数の上限は従来の 1,000,000 から変えていないこと。"""
    assert units.UNIT_SPECS["count"].max_value == 1_000_000
    with pytest.raises(units.UnitError):
        units.normalise_range("個", 0, 1_000_001)


def test_ranges_are_validated_after_normalisation() -> None:
    with pytest.raises(units.UnitError) as caught:
        units.normalise_range("m", 13, 12)
    assert caught.value.code == "reversed_range"

    with pytest.raises(units.UnitError) as caught:
        units.normalise_range("m", -1, 12)
    assert caught.value.code == "negative_quantity"


# =====================================================================
# 実運用の入口を、連続量が実際に通る
# =====================================================================


def _orchestrator() -> InferenceOrchestrator:
    return InferenceOrchestrator(
        {
            "det": MethodPolicy(calibrated=True, max_strength="strong"),
            "spec": MethodPolicy(calibrated=True, max_strength="strong"),
        },
        {"drawing-A": "sha256:drawing-A", "spec-A": "sha256:spec-A"},
    )


def _evidence(unit: str, rng: list[object], *, source: str, axis: str, method: str) -> dict:
    return {
        "target": "pipe_length", "count_range": rng, "unit": unit,
        "source_id": source, "source_fingerprint": f"sha256:{source}",
        "axis_id": axis, "method_id": method,
        "strength": "strong", "status": "confident", "calibrated": True,
    }


@pytest.mark.parametrize(
    ("label", "unit_a", "range_a", "unit_b", "range_b", "expected"),
    [
        ("長さ(小数のメートル)", "m", [12.5, 12.5], "m", [12.5, 12.5], (12_500, 12_500)),
        ("面積(㎡と平方メートル)", "㎡", [100, 100], "平方メートル", [100, 100], (1_000_000, 1_000_000)),
        ("金額(円と万円)", "円", [1_500_000, 1_500_000], "万円", [150, 150], (1_500_000, 1_500_000)),
    ],
)
def test_continuous_quantities_reach_tier1_through_the_real_entry_point(
    label: str, unit_a: str, range_a: list, unit_b: str, range_b: list,
    expected: tuple[int, int],
) -> None:
    """修正前は入口で ``unknown_unit`` に落ちていた組み合わせが階層1に届くこと。

    ``円`` と ``万円`` が一致するのは正しい。同じ正規形(``yen``)に正規化された
    うえで数値が一致しているので、単位の取り違えではなく本当に同じ金額である。
    """
    result = _orchestrator().process({
        "trace_id": f"cont-{label}", "element_id": "pipe-1",
        "evidence": [
            _evidence(unit_a, range_a, source="drawing-A", axis="image", method="det"),
            _evidence(unit_b, range_b, source="spec-A", axis="text", method="spec"),
        ],
        "relations": [],
    })

    assert not result.is_invalid, label
    assert result.decision is not None
    assert result.decision.tier == 1, label
    assert result.decision.confirmed_range == expected, label


@pytest.mark.parametrize(
    ("label", "unit", "rng", "expected_code"),
    [
        ("1mm より細かい長さ", "m", [12.5551, 12.6], "value_finer_than_unit_step"),
        ("個数に小数", "個", [4.5, 5], "value_finer_than_unit_step"),
        ("面積が上限超え", "㎡", [200_000, 200_000], "quantity_too_large"),
        ("未知の単位", "尺", [10, 10], "unknown_unit"),
        ("文字列の数量", "m", ["5", "5"], "invalid_quantity"),
    ],
)
def test_the_entry_point_refuses_what_it_cannot_represent(
    label: str, unit: str, rng: list, expected_code: str
) -> None:
    result = _orchestrator().process({
        "trace_id": f"bad-{label}", "element_id": "x",
        "evidence": [_evidence(unit, rng, source="drawing-A", axis="image", method="det")],
        "relations": [],
    })
    assert result.is_invalid, label
    codes = [code for event in result.events for code in event.reason_codes]
    assert f"evidence[0]:{expected_code}" in codes, (label, codes)


def test_mixing_a_length_and_an_area_is_still_refused() -> None:
    """正規形が違えば、次元が違うので当然通さないこと。"""
    result = _orchestrator().process({
        "trace_id": "mixed", "element_id": "x",
        "evidence": [
            _evidence("m", [12, 12], source="drawing-A", axis="image", method="det"),
            _evidence("㎡", [12, 12], source="spec-A", axis="text", method="spec"),
        ],
        "relations": [],
    })
    assert result.is_invalid
    codes = [code for event in result.events for code in event.reason_codes]
    assert "unit_mismatch" in codes


# =====================================================================
# キラークエスチョンが、連続量のレンジで爆発しない
# =====================================================================


def test_a_wide_continuous_range_is_not_enumerated() -> None:
    """列挙上限を超えるレンジは質問にならないこと。

    実測: 修正前は配管延長 12.0〜13.0m(1001候補)のスコア計算に4.8秒かかり、
    床面積 100㎡ を cm² で ±1% 見ると 20001候補になっていた。
    """
    solver = ConsistencySolver()
    solver.add_variable("pipe_total", 12_000, 13_000, axis="image", unit="mm")
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.PRECISE)

    assert engine.next_question() is None


def test_a_non_enumerable_element_stays_unresolved() -> None:
    """列挙できないことを理由に、確定済みへ回さないこと。

    ここがバグ②と同じ性質の落とし穴である。質問できないからといって
    確定済み金額に計上すると、人が見ていない数量が「確定」と報告される。
    """
    solver = ConsistencySolver()
    solver.add_variable("floor_area", 990_000, 1_010_000, axis="image", unit="cm2")
    engine = KillerQuestionEngine(
        solver, unit_prices={"floor_area": 0.5}, mode=PrecisionMode.PRECISE
    )

    session = engine.run(lambda question: question.candidate_values[0])

    assert session.question_count == 0
    assert session.stopped_reason == "candidates_not_enumerable"
    assert session.remaining_unresolved == ("floor_area",)
    assert engine.confirmed_amount(session.final_result) == 0.0
    assert engine.coverage(session.final_result) == 0.0


def test_a_narrow_continuous_range_is_still_asked_about() -> None:
    """上限以内なら、連続量でも従来どおり質問されること。

    上限が「連続量は一律に諦める」という実装になっていないことの確認。
    """
    solver = ConsistencySolver()
    solver.add_variable("pipe_total", 12_000, 12_010, axis="image", unit="mm")
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.PRECISE)

    question = engine.next_question()
    assert question is not None
    assert question.variable == "pipe_total"
    assert len(question.candidate_values) == 11


def test_the_enumeration_limit_is_the_boundary_it_claims() -> None:
    """上限のちょうど境界で挙動が切り替わること(off-by-one の確認)。"""
    limit = _MAX_CANDIDATE_ENUMERATION

    at_limit = ConsistencySolver()
    at_limit.add_variable("q", 0, limit - 1, axis="image", unit="mm")
    question = KillerQuestionEngine(at_limit, mode=PrecisionMode.PRECISE).next_question()
    assert question is not None
    assert len(question.candidate_values) == limit

    over_limit = ConsistencySolver()
    over_limit.add_variable("q", 0, limit, axis="image", unit="mm")
    assert KillerQuestionEngine(over_limit, mode=PrecisionMode.PRECISE).next_question() is None


def test_discrete_counts_are_unaffected_by_the_limit() -> None:
    """離散カウントの従来の挙動が変わっていないこと。"""
    solver = ConsistencySolver()
    solver.add_variable("door_count", 4, 6, axis="image", unit="count")
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.PRECISE)

    session = engine.run(lambda question: 5)

    assert session.question_count == 1
    assert session.stopped_reason == "all_resolved"
