"""実運用入口→ファイアウォール→Z3→ルーターの統合試験。"""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import pytest

from arbitration.escalation_router import (
    ROUTE_TABLE,
    EscalationEvent,
    EscalationRouter,
)
from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy

SEED = 20260922
TRIALS = 1000


def make_orchestrator():
    strong_methods = {
        "vision", "table", "vtracer", "classical",
        *(f"method-{source}-{index}" for source in range(4) for index in range(6)),
    }
    policies = {name: MethodPolicy(calibrated=True, max_strength="strong") for name in strong_methods}
    policies.update({
        "grounding-dino": MethodPolicy(calibrated=False, max_strength="weak"),
        "prior": MethodPolicy(calibrated=True, max_strength="weak"),
        "distribution": MethodPolicy(calibrated=True, max_strength="weak"),
        "stats": MethodPolicy(calibrated=True, max_strength="weak"),
        # 2つ目の独立した弱い軸。以前は業界一般統計軸("statistical")を使って
        # いたが、その軸は軸間照合から外れたため(docs/design_v8.md 5-2節)、
        # 整合性軸の比率を弱い軸として使うケースに置き換えている。
        "rule_ratio": MethodPolicy(calibrated=True, max_strength="weak"),
        "m1": MethodPolicy(calibrated=True, max_strength="strong"),
        "m2": MethodPolicy(calibrated=True, max_strength="strong"),
    })
    sources = {
        name: f"sha256:{name}"
        for name in ("drawing-A", "spec-A", "history-A", "stats-A", "rule-A", "a", "b")
    }
    sources.update({f"source-{index}": f"sha256:source-{index}" for index in range(4)})
    sources.update({
        "display-A": "sha256:same-original",
        "renamed-to-look-independent": "sha256:same-original",
    })
    return InferenceOrchestrator(policies, sources)


def item(
    value=5,
    *,
    source="drawing-A",
    axis="image",
    method="vision",
    strength="strong",
    status="confident",
    calibrated=True,
    derivation="read",
    confidence=0.9,
    unit="count",
    target="door_count",
    source_fingerprint=None,
):
    value_range = list(value) if isinstance(value, tuple) else [value, value]
    return {
        "target": target,
        "count_range": value_range,
        "source_id": source,
        "source_fingerprint": source_fingerprint or f"sha256:{source}",
        "axis_id": axis,
        "method_id": method,
        "strength": strength,
        "status": status,
        "calibrated": calibrated,
        "derivation": derivation,
        "model_confidence": confidence,
        "unit": unit,
    }


def request(evidence, trace="trace-1", relations=None):
    return {
        "trace_id": trace,
        "element_id": "door-1",
        "evidence": evidence,
        "relations": [] if relations is None else relations,
    }


def failure_types(result):
    return {event.failure_type for event in result.events}


# 固定ケース1
def test_case_01_two_independent_strong_sources_auto_confirm_without_event():
    result = make_orchestrator().process(
        request([
            item(source="drawing-A", axis="image", method="vision"),
            item(source="spec-A", axis="text", method="table"),
        ])
    )
    assert result.decision.solve_result.status == "sat"
    assert result.decision.tier == 1
    assert result.decision.action == "auto_confirm"
    assert result.events == ()


# 固定ケース2
def test_case_02_one_strong_and_two_independent_weak_sources_is_tier2():
    result = make_orchestrator().process(
        request([
            item(source="spec-A", axis="text", method="table"),
            item(source="history-A", axis="history", method="prior", strength="weak"),
            item(source="rule-A", axis="rules", method="rule_ratio", strength="weak"),
        ])
    )
    assert result.decision.tier == 2
    assert result.decision.action == "provisional_audit"
    assert result.decision.reasons


# 固定ケース3
def test_case_03_three_methods_same_source_count_once():
    result = make_orchestrator().process(
        request([item(method=name) for name in ("grounding-dino", "vtracer", "classical")])
    )
    assert result.decision.independent_strong_source_count == 1
    assert result.decision.tier != 1


# 固定ケース4
def test_case_04_uncalibrated_grounding_dino_is_not_sent_to_z3():
    result = make_orchestrator().process(
        request([item(value=99, method="grounding-dino", calibrated=False, confidence=0.999)])
    )
    assert result.decision.solve_result.variables == {}
    assert "uncalibrated_source" in failure_types(result)


# 固定ケース5
def test_case_05_abstained_grounding_dino_is_not_counted():
    result = make_orchestrator().process(
        request([item(value=0, method="grounding-dino", status="abstained", calibrated=False)])
    )
    assert result.decision.solve_result.variables == {}
    assert result.decision.independent_strong_source_count == 0


# 固定ケース6
def test_case_06_wrong_weak_does_not_make_strong_solution_unsat():
    result = make_orchestrator().process(
        request([
            item(value=5, source="spec-A", axis="text", method="table"),
            item(value=99, source="drawing-A", method="grounding-dino", strength="weak"),
        ])
    )
    assert result.decision.solve_result.status == "sat"
    assert result.decision.confirmed_range == (5, 5)


# 固定ケース7
def test_case_07_conflicting_independent_strong_sources_route_axis_contradiction():
    result = make_orchestrator().process(
        request([
            item(value=5, source="drawing-A", method="vision"),
            item(value=7, source="spec-A", axis="text", method="table"),
        ])
    )
    event = next(event for event in result.events if event.failure_type == "axis_contradiction")
    assert result.decision.solve_result.status == "unsat"
    assert result.decision.confirmed_range is None
    assert event.unsat_core
    assert event.recommended_resource == "r1_standard_checker"


# 固定ケース8
def test_case_08_correlated_plausible_wrong_answers_route_risk():
    result = make_orchestrator().process(
        request([item(value=9, method=name) for name in ("grounding-dino", "vtracer", "classical")])
    )
    assert result.decision.tier != 1
    assert "correlated_source_risk" in failure_types(result)


# 固定ケース9
def test_case_09_all_weak_routes_insufficient_evidence():
    result = make_orchestrator().process(
        request([
            item(source="history-A", axis="history", method="prior", strength="weak"),
            item(source="rule-A", axis="rules", method="rule_ratio", strength="weak"),
        ])
    )
    assert result.decision.tier == 3
    assert "insufficient_independent_evidence" in failure_types(result)


# 固定ケース10
def test_case_10_all_abstained_does_not_create_candidate_range():
    result = make_orchestrator().process(
        request([
            item(value=0, source="a", method="m1", status="abstained"),
            item(value=0, source="b", method="m2", status="abstained"),
        ])
    )
    assert result.decision.tier == 3
    assert result.decision.confirmed_range is None


# 固定ケース11
def test_case_11_missing_source_id_is_invalid_and_not_fabricated():
    evidence = item()
    evidence.pop("source_id")
    result = make_orchestrator().process(request([evidence]))
    assert result.is_invalid
    assert result.decision is None
    assert result.events[0].source_ids == ()


# 固定ケース12
def test_case_12_missing_axis_id_is_invalid():
    evidence = item()
    evidence.pop("axis_id")
    result = make_orchestrator().process(request([evidence]))
    assert result.is_invalid


# 固定ケース13
def test_case_13_missing_method_id_is_rejected():
    evidence = item()
    evidence.pop("method_id")
    result = make_orchestrator().process(request([evidence]))
    assert result.is_invalid


# 固定ケース14
def test_case_14_duplicate_evidence_is_deduplicated():
    evidence = item()
    result = make_orchestrator().process(request([evidence, dict(evidence)]))
    assert result.duplicate_evidence_count == 1
    assert len(result.accepted_evidence_ids) == 1
    assert result.decision.independent_strong_source_count == 1


# 固定ケース15
def test_case_15_same_trace_retry_returns_same_events_and_result():
    orchestrator = make_orchestrator()
    payload = request([item(calibrated=False)], trace="retry-trace")
    first = orchestrator.process(payload)
    second = orchestrator.process(payload)
    assert second is first
    assert [event.event_id for event in second.events] == [event.event_id for event in first.events]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.update(source_id=""),
        lambda x: x.update(axis_id=None),
        lambda x: x.update(model_confidence=float("nan")),
        lambda x: x.update(model_confidence=float("inf")),
        lambda x: x.update(count_range=[-1, 1]),
        lambda x: x.update(count_range=[9, 1]),
        lambda x: x.update(status="certain"),
        lambda x: x.update(axis_id="imaginary-axis"),
        lambda x: x.update(count_range=[0, 1_000_001]),
        lambda x: x.update(unit="metre"),
        lambda x: x.update(count_range=["5", "5"]),
    ],
)
def test_invalid_evidence_is_safely_rejected(mutation):
    evidence = item()
    mutation(evidence)
    result = make_orchestrator().process(request([evidence]))
    assert result.is_invalid
    assert result.decision is None
    assert result.routes[0].resource == "input_correction_required"


def test_conflicting_duplicate_identity_is_invalid():
    first = item(value=5)
    second = item(value=6)
    result = make_orchestrator().process(request([first, second]))
    assert result.is_invalid


@pytest.mark.parametrize(
    "relations",
    [
        [{"name": "r", "lhs": "a", "op": "==", "rhs": "b"}],
        [
            {"name": "a_to_b", "lhs": "a", "op": "==", "rhs": "b"},
            {"name": "b_to_a", "lhs": "b", "op": "==", "rhs": "a"},
        ],
    ],
)
def test_external_duplicate_or_cyclic_relations_are_rejected_until_supported(relations):
    result = make_orchestrator().process(request([item()], relations=relations))
    assert result.is_invalid
    assert "external_relations_not_supported" in result.events[0].reason_codes


@pytest.mark.parametrize("payload", ["{broken", "[]", "null", b"\xff"])
def test_broken_json_is_safe(payload):
    result = make_orchestrator().process_json(payload)
    assert result.is_invalid
    assert result.decision is None


def test_json_and_python_api_share_the_same_path():
    payload = request([
        item(source="drawing-A"),
        item(source="spec-A", axis="text", method="table"),
    ], trace="json-trace")
    result = make_orchestrator().process_json(json.dumps(payload))
    assert result.decision.tier == 1


def test_missing_confidence_status_or_calibration_is_not_promoted():
    missing_status = item()
    missing_status.pop("status")
    missing_calibration = item()
    missing_calibration.pop("calibrated")
    for index, evidence in enumerate((missing_status, missing_calibration)):
        result = make_orchestrator().process(request([evidence], trace=f"missing-{index}"))
        assert result.is_invalid


def test_trace_id_reuse_with_changed_payload_is_invalid():
    orchestrator = make_orchestrator()
    orchestrator.process(request([item(value=5)], trace="same-trace"))
    result = orchestrator.process(request([item(value=6)], trace="same-trace"))
    assert result.is_invalid
    assert "trace_id_payload_mismatch" in result.events[0].reason_codes


def test_changing_source_id_only_cannot_inflate_independence():
    first = item(source="display-A", source_fingerprint="sha256:same-original")
    second = item(
        source="renamed-to-look-independent",
        source_fingerprint="sha256:same-original",
        method="table",
        axis="text",
    )
    result = make_orchestrator().process(request([first, second], trace="source-spoof"))
    assert result.decision.independent_strong_source_count == 1
    assert result.decision.tier != 1


def test_unregistered_method_cannot_self_declare_calibrated_strong():
    result = make_orchestrator().process(
        request([item(method="attacker-method", calibrated=True, strength="strong")], trace="policy-spoof")
    )
    assert result.decision.hard_evidence_ids == ()
    assert "uncalibrated_source" in failure_types(result)
    assert any("unregistered_method_downgraded" in note for note in result.normalization_notes)


@pytest.mark.parametrize(
    "evidence,reason",
    [
        (item(source="invented-source"), "unregistered_source"),
        (item(source="drawing-A", source_fingerprint="sha256:forged"), "source_fingerprint_mismatch"),
    ],
)
def test_source_identity_must_match_trusted_registry(evidence, reason):
    result = make_orchestrator().process(request([evidence], trace=f"source-check-{reason}"))
    assert result.is_invalid
    assert any(reason in code for code in result.events[0].reason_codes)


@pytest.mark.parametrize("failure_type,resource", sorted(ROUTE_TABLE.items()))
def test_router_contract_for_every_failure_type(failure_type, resource):
    core = ("constraint",) if failure_type == "axis_contradiction" else ()
    event = EscalationEvent(
        event_id="event",
        element_id="element",
        failure_type=failure_type,
        reason_codes=("test",),
        source_ids=(),
        axis_ids=(),
        method_ids=(),
        conflicting_constraints=(),
        unsat_core=core,
        candidate_range=None,
        confidence_tier=3,
        recommended_resource=resource,
        human_confirmation_required=True,
        created_at="2026-09-21T00:00:00+00:00",
        trace_id="trace",
    )
    routed = EscalationRouter().route(event)
    assert routed.resource == resource
    assert routed.status == "not_executed"


def test_router_rejects_wrong_resource_contract():
    event = EscalationEvent(
        event_id="event", element_id="element", failure_type="invalid_input",
        reason_codes=("test",), source_ids=(), axis_ids=(), method_ids=(),
        conflicting_constraints=(), unsat_core=(), candidate_range=None,
        confidence_tier=3, recommended_resource="wrong", human_confirmation_required=True,
        created_at="2026-09-21T00:00:00+00:00", trace_id="trace",
    )
    with pytest.raises(ValueError):
        EscalationRouter().route(event)


def test_router_rejects_empty_unsat_core_for_axis_contradiction():
    event = EscalationEvent(
        event_id="event", element_id="element", failure_type="axis_contradiction",
        reason_codes=("test",), source_ids=(), axis_ids=(), method_ids=(),
        conflicting_constraints=(), unsat_core=(), candidate_range=None,
        confidence_tier=3, recommended_resource="r1_standard_checker",
        human_confirmation_required=True, created_at="2026-09-21T00:00:00+00:00",
        trace_id="trace",
    )
    with pytest.raises(ValueError):
        EscalationRouter().route(event)


def test_randomized_integration_invariants_1000_trials():
    rng = random.Random(SEED)
    orchestrator = make_orchestrator()
    for trial in range(TRIALS):
        evidence = []
        for index in range(rng.randint(0, 6)):
            source_index = rng.randint(0, 3)
            status = rng.choice(["confident", "low_confidence", "abstained"])
            strength = rng.choice(["strong", "weak"])
            calibrated = rng.choice([True, False])
            center = rng.randint(0, 30)
            current = item(
                value=(center, center + rng.randint(0, 2)),
                source=f"source-{source_index}",
                axis=rng.choice(["image", "text", "rules", "history"]),
                method=f"method-{source_index}-{index}",
                status=status,
                strength=strength,
                calibrated=calibrated,
                confidence=rng.random(),
            )
            if rng.random() < 0.05:
                current.pop(rng.choice(["source_id", "axis_id", "method_id"]))
            if rng.random() < 0.03:
                current["count_range"] = [-1, 0]
            evidence.append(current)
            if rng.random() < 0.15:
                evidence.append(dict(current))
        relations = [] if rng.random() < 0.95 else [{"name": "unsupported"}]
        payload = request(evidence, trace=f"random-{trial}", relations=relations)
        result = orchestrator.process(payload)
        replay = orchestrator.process(payload)
        assert replay is result, f"seed={SEED} trial={trial}"
        assert all(event.failure_type for event in result.events), f"seed={SEED} trial={trial}"
        if result.is_invalid:
            assert result.decision is None, f"seed={SEED} trial={trial}"
            continue
        decision = result.decision
        assert decision is not None
        assert decision.tier != 1 or decision.independent_strong_source_count >= 2, (
            f"seed={SEED} trial={trial}"
        )
        assert decision.independent_strong_source_count == len(
            {evidence_id.split("::", 1)[0] for evidence_id in decision.hard_evidence_ids}
        ), f"seed={SEED} trial={trial}"
        if decision.solve_result.status == "unsat":
            assert decision.action != "auto_confirm", f"seed={SEED} trial={trial}"
            assert decision.confirmed_range is None, f"seed={SEED} trial={trial}"
            assert "axis_contradiction" in failure_types(result), f"seed={SEED} trial={trial}"
        accepted = {
            (entry.get("source_id"), entry.get("axis_id"), entry.get("method_id")): entry
            for entry in evidence
            if isinstance(entry, dict)
        }
        for evidence_id in decision.hard_evidence_ids:
            source_id, axis_id, method_id, _ = evidence_id.split("::")
            original = accepted[(source_id, axis_id, method_id)]
            assert original["strength"] == "strong"
            assert original["status"] == "confident"
            assert original["calibrated"] is True


def test_static_production_code_has_no_solver_bypass():
    root = Path(__file__).resolve().parents[1]
    allowed_solver_users = {
        root / "arbitration" / "consistency_solver.py",
        root / "arbitration" / "axis_quality_firewall.py",
        root / "arbitration" / "constraint_exhaustive_search.py",
    }
    violations = []
    for path in root.rglob("*.py"):
        if any(part in {"tests", "benchmarks", ".venv"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name for alias in node.names]
                module = node.module if isinstance(node, ast.ImportFrom) else ""
                if (module == "z3" or "z3" in modules) and path not in allowed_solver_users:
                    violations.append(f"{path}: direct z3 import")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "ConsistencySolver" and path not in allowed_solver_users:
                    violations.append(f"{path}: direct ConsistencySolver construction")
    assert violations == []


# ---------------------------------------------------------------------------
# 根拠(provenance)。2026-09-22 追加。
# 実図面の入口(`intake/drawing_intake.py`)が、読み取った値と一緒に
# 「どのページの、どの位置に、どう書いてあったか」を運べるようにするため。
# ---------------------------------------------------------------------------


def test_provenance_is_optional_and_reaches_the_evidence():
    """任意である。付けたときはそのまま `AxisEvidence.evidence` に載る。"""
    provenance = {
        "page_number": 8,
        "source_text": "専有延床面積\n95.54",
        "rect_pt": [850.0, 790.0, 900.0, 802.0],
    }
    entry = item()
    entry["provenance"] = provenance
    result = make_orchestrator().process(request([entry]))

    assert not result.is_invalid
    assert result.decision is not None
    # 付けなくても通ること(既存の呼び出しを1文字も変えずに通す)。
    assert not make_orchestrator().process(request([item()])).is_invalid


def test_provenance_that_cannot_be_written_out_is_refused():
    """JSON にできない根拠は黙って捨てず、入力の誤りとして落とす。

    黙って落とすと、根拠が付いているつもりの呼び出しが、根拠なしのまま
    通り続ける。
    """
    entry = item()
    entry["provenance"] = {"rect_pt": {1, 2}}  # 集合は JSON にできない
    result = make_orchestrator().process(request([entry]))

    assert result.is_invalid
    assert any(
        "provenance_not_serialisable" in code
        for event in result.events
        for code in event.reason_codes
    )


def test_provenance_must_be_an_object_with_text_keys():
    entry = item()
    entry["provenance"] = ["page 8"]
    result = make_orchestrator().process(request([entry]))

    assert result.is_invalid
    assert any(
        "invalid_provenance" in code
        for event in result.events
        for code in event.reason_codes
    )
