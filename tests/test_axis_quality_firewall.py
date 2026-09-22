"""段階B追加検証: 軸品質ファイアウォールの固定10ケースと乱数耐性試験。"""

from __future__ import annotations

import random

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from axes.image_axis.grounding_dino_adapter import SymbolCountReading


TARGET = "door_count"
RANDOM_SEED = 20260921
RANDOM_TRIALS = 500


def _evidence(
    value: int | tuple[int, int],
    *,
    source_id: str,
    axis_id: str,
    method_id: str,
    strength: str = "strong",
    status: str = "confident",
    calibrated: bool = True,
    model_confidence: float | None = None,
) -> AxisEvidence:
    count_range = value if isinstance(value, tuple) else (value, value)
    return AxisEvidence(
        derivation="read",
        unit="count",
        target=TARGET,
        count_range=count_range,
        source_id=source_id,
        axis_id=axis_id,
        method_id=method_id,
        strength=strength,
        status=status,
        calibrated=calibrated,
        model_confidence=model_confidence,
    )


def _solution_signature(result) -> tuple:
    return (
        result.status,
        tuple(
            sorted((name, item.solved_range) for name, item in result.variables.items())
        ),
        tuple(sorted(result.conflicting_constraints)),
    )


# ケース1
def test_case_01_two_calibrated_independent_strong_axes_auto_confirm() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table"),
        ]
    )
    assert decision.tier == 1
    assert decision.action == "auto_confirm"
    assert decision.confirmed_range == (5, 5)
    assert decision.independent_strong_source_count == 2


# ケース2
def test_case_02_two_axes_with_same_source_are_not_independent() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(5, source_id="drawing-A", axis_id="geometry", method_id="vtracer"),
        ]
    )
    assert decision.tier != 1
    assert decision.independent_strong_source_count == 1
    assert any("同一データ源" in reason for reason in decision.reasons)


# ケース3
def test_case_03_three_image_methods_are_three_lenses_inside_one_source() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="grounding-dino"),
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vtracer"),
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="classical"),
        ]
    )
    assert decision.method_count == 3
    assert decision.independent_strong_source_count == 1
    assert decision.tier != 1


# ケース4
def test_case_04_three_weak_methods_from_one_image_count_as_one_advisory_source() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="spec-A", axis_id="text", method_id="table"),
            *[
                _evidence(
                    5,
                    source_id="drawing-A",
                    axis_id="image",
                    method_id=method,
                    strength="weak",
                )
                for method in ("grounding-dino", "vtracer", "classical")
            ],
        ]
    )
    assert decision.independent_advisory_source_count == 1
    assert decision.tier == 3
    assert any("同一データ源" in reason for reason in decision.reasons)


# ケース5
def test_case_05_wrong_weak_grounding_dino_cannot_change_z3_solution_or_core() -> None:
    firewall = AxisQualityFirewall()
    strong = _evidence(5, source_id="spec-A", axis_id="text", method_id="table")
    baseline = firewall.assess([strong])
    with_weak = firewall.assess(
        [
            strong,
            _evidence(
                99,
                source_id="drawing-A",
                axis_id="image",
                method_id="grounding-dino",
                strength="weak",
            ),
        ]
    )
    assert _solution_signature(with_weak.solve_result) == _solution_signature(
        baseline.solve_result
    )
    assert with_weak.confirmed_range == baseline.confirmed_range == (5, 5)
    assert with_weak.solve_result.conflicting_constraints == ()


# ケース6
def test_case_06_high_internal_confidence_cannot_override_failed_calibration() -> None:
    grounding_dino = _evidence(
        99,
        source_id="drawing-A",
        axis_id="image",
        method_id="grounding-dino",
        strength="strong",
        calibrated=False,
        model_confidence=0.99,
    )
    decision = AxisQualityFirewall().assess([grounding_dino])
    assert grounding_dino.evidence_id in decision.advisory_evidence_ids
    assert grounding_dino.evidence_id not in decision.hard_evidence_ids
    assert decision.tier == 3
    assert any("実測校正" in reason for reason in decision.reasons)


# ケース7
def test_case_07_abstained_grounding_dino_creates_no_z3_variable_or_evidence_count() -> None:
    grounding_dino = _evidence(
        (0, 0),
        source_id="drawing-A",
        axis_id="image",
        method_id="grounding-dino",
        status="abstained",
        calibrated=False,
    )
    decision = AxisQualityFirewall().assess([grounding_dino])
    assert decision.solve_result.variables == {}
    assert decision.independent_strong_source_count == 0
    assert decision.independent_advisory_source_count == 0
    assert grounding_dino.evidence_id in decision.abstained_evidence_ids
    assert decision.confirmed_range is None


# ケース8
def test_case_08_conflicting_independent_strong_axes_escalate_as_axis_contradiction() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(5, source_id="drawing-A", axis_id="image", method_id="vision"),
            _evidence(7, source_id="spec-A", axis_id="text", method_id="table"),
        ]
    )
    assert decision.solve_result.status == "unsat"
    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "axis_contradiction"
    assert decision.escalation.conflicting_constraints


# ケース9
def test_case_09_all_weak_or_abstained_requires_review() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(
                5,
                source_id="history-A",
                axis_id="history",
                method_id="prior",
                strength="weak",
            ),
            _evidence(
                (0, 0),
                source_id="drawing-A",
                axis_id="image",
                method_id="grounding-dino",
                status="abstained",
                calibrated=False,
            ),
        ]
    )
    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None


# ケース10
def test_case_10_correlated_plausible_wrong_answers_do_not_auto_confirm() -> None:
    decision = AxisQualityFirewall().assess(
        [
            _evidence(9, source_id="drawing-A", axis_id="image", method_id="grounding-dino"),
            _evidence(9, source_id="drawing-A", axis_id="image", method_id="vtracer"),
            _evidence(9, source_id="drawing-A", axis_id="image", method_id="classical"),
        ]
    )
    assert decision.solve_result.status == "sat"
    assert decision.tier != 1
    assert decision.action != "auto_confirm"
    assert any("相関誤り" in reason for reason in decision.reasons)


def test_randomized_firewall_invariants_500_trials() -> None:
    rng = random.Random(RANDOM_SEED)
    firewall = AxisQualityFirewall()

    for trial in range(RANDOM_TRIALS):
        truth = rng.randint(0, 100)
        source_count = rng.randint(0, 4)
        hard: list[AxisEvidence] = []
        non_hard: list[AxisEvidence] = []

        for source_index in range(source_count):
            source_id = f"source-{source_index}"
            method_count = rng.randint(1, 4)
            correct = rng.choice([True, False])
            center = truth if correct else truth + rng.choice([-7, -3, 3, 7])
            lower = center - rng.randint(0, 2)
            upper = center + rng.randint(0, 2)
            for method_index in range(method_count):
                status = rng.choice(["confident", "low_confidence", "abstained"])
                strength = rng.choice(["strong", "weak"])
                calibrated = rng.choice([True, False])
                evidence = _evidence(
                    (0, 0) if status == "abstained" else (lower, upper),
                    source_id=source_id,
                    axis_id=rng.choice(["image", "text", "rules", "history"]),
                    method_id=f"method-{source_index}-{method_index}",
                    strength=strength,
                    status=status,
                    calibrated=calibrated,
                    model_confidence=rng.random(),
                )
                if evidence.is_hard_eligible:
                    hard.append(evidence)
                else:
                    non_hard.append(evidence)

        hard_only = firewall.assess(hard)
        full = firewall.assess(hard + non_hard)
        assert _solution_signature(full.solve_result) == _solution_signature(
            hard_only.solve_result
        ), f"seed={RANDOM_SEED} trial={trial}"

        # 同じデータ源・同じレンジの手法を増やしても独立軸数は増えない。
        if hard:
            original = hard[0]
            duplicate = _evidence(
                original.count_range,
                source_id=original.source_id,
                axis_id="another-library-axis-label",
                method_id=f"duplicate-{trial}",
            )
            duplicated = firewall.assess(hard + [duplicate])
            assert (
                duplicated.independent_strong_source_count
                == hard_only.independent_strong_source_count
            ), f"seed={RANDOM_SEED} trial={trial}"

        assert full.tier != 1 or full.independent_strong_source_count >= 2, (
            f"seed={RANDOM_SEED} trial={trial}"
        )
        if full.solve_result.status == "unsat":
            assert full.confirmed_range is None, f"seed={RANDOM_SEED} trial={trial}"
            assert full.action != "auto_confirm", f"seed={RANDOM_SEED} trial={trial}"

        high_confidence_uncalibrated = _evidence(
            truth + 50,
            source_id=f"uncalibrated-{trial}",
            axis_id="image",
            method_id="grounding-dino",
            calibrated=False,
            model_confidence=0.999,
        )
        uncalibrated = firewall.assess(hard + [high_confidence_uncalibrated])
        assert high_confidence_uncalibrated.evidence_id not in uncalibrated.hard_evidence_ids


def test_legacy_solver_api_cannot_promote_weak_or_low_confidence_readings() -> None:
    """旧APIを直接使う経路にも weak/low-confidence の漏洩防止を置く。"""
    solver = ConsistencySolver()
    low = SymbolCountReading(
        category="door",
        prompt="door",
        count_range=(90, 100),
        status="low_confidence",
        detections=(),
        evidence={},
    )
    weak = SymbolCountReading(
        category="door",
        prompt="door",
        count_range=(80, 90),
        status="confident",
        detections=(),
        evidence={},
    )
    solver.add_variable_from_reading("low", low, axis="image")
    solver.add_variable_from_reading("weak", weak, axis="history", strength="weak")
    result = solver.solve()
    assert result.variables == {}
    assert len(result.advisories) == 2
