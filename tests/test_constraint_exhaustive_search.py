"""段階B追加検証3: 制約全探索の固定・難問・E2E・性質試験。"""

from __future__ import annotations

import random

from arbitration.constraint_exhaustive_search import (
    ConstraintExhaustiveSearch,
    ExhaustiveSearchRequest,
    LinearExpression,
    SearchRelation,
)
from arbitration.inference_orchestrator import InferenceOrchestrator, MethodPolicy

SEED = 20260923
TRIALS = 2000


def expr(**terms):
    constant = terms.pop("constant", 0)
    return LinearExpression(tuple(sorted(terms.items())), constant)


def relation(name, lhs, op, rhs):
    return SearchRelation(name, lhs, op, rhs)


def req(values, *, ranges=None, relations=(), hard=(), advisory=(), trace="trace", event="event", **kwargs):
    element_ids = tuple(sorted(set(values) | set(ranges or {})))
    return ExhaustiveSearchRequest(
        trace_id=trace,
        event_id=event,
        element_ids=element_ids,
        candidate_values={name: tuple(items) for name, items in values.items()},
        candidate_ranges={} if ranges is None else ranges,
        relations=tuple(relations),
        hard_constraints=tuple(hard),
        advisory_information=tuple(advisory),
        source_ids=("source-A",),
        axis_ids=("image",),
        method_ids=("vision",),
        original_confidence_tiers=(3,),
        **kwargs,
    )


def solve(request):
    return ConstraintExhaustiveSearch().search(request)


def eq(name, variable, value):
    return relation(name, expr(**{variable: 1}), "==", expr(constant=value))


# 固定ケース1
def test_case_01_constraint_produces_unique_solution():
    result = solve(req({"a": (1, 2), "b": (1, 2)}, relations=(
        relation("a_plus_b", expr(a=1, b=1), "==", expr(constant=3)),
        relation("a_lt_b", expr(a=1), "<", expr(b=1)),
    )))
    assert result.status == "unique_solution" and result.is_unique


# 固定ケース2
def test_case_02_multiple_solutions_have_no_representative():
    result = solve(req({"a": (1, 2), "b": (1, 2)}, relations=(
        relation("sum", expr(a=1, b=1), "==", expr(constant=3)),
    )))
    assert result.status == "multiple_solutions"
    assert not result.is_unique and result.solution_count == 2


# 固定ケース3
def test_case_03_no_solution_has_no_provisional_value():
    result = solve(req({"a": (1, 2)}, hard=(eq("a_is_9", "a", 9),)))
    assert result.status == "no_solution"
    assert result.solutions == () and result.narrowed_candidates == {}


# 固定ケース4
def test_case_04_all_candidates_valid_reports_no_reduction():
    result = solve(req({"a": (1, 2), "b": (3, 4)}))
    assert result.status == "completed_without_reduction"
    assert "constraints_did_not_reduce_candidates" in result.reason_codes


# 固定ケース5
def test_case_05_total_equals_parts_reduces_to_unique():
    result = solve(req({"total": (5,), "a": (1, 2), "b": (3, 4)}, relations=(
        relation("total_parts", expr(total=1), "==", expr(a=1, b=1)),
        eq("a_is_1", "a", 1),
    )))
    assert result.status == "unique_solution"
    assert result.eliminated_candidates["a"] == (2,)


# 固定ケース6
def test_case_06_swappable_parts_are_not_unique():
    result = solve(req({"total": (5,), "a": (1, 2), "b": (3, 4)}, relations=(
        relation("total_parts", expr(total=1), "==", expr(a=1, b=1)),
    )))
    assert result.status == "multiple_solutions" and result.solution_count == 2


# 固定ケース7
def test_case_07_same_source_metadata_never_adds_independence():
    request = req({"a": (1, 2)}, hard=(eq("a_is_1", "a", 1),))
    request = ExhaustiveSearchRequest(**{**request.__dict__, "source_ids": ("same", "same")})
    result = solve(request)
    assert result.independence_added is False


# 固定ケース8
def test_case_08_all_weak_remains_not_upgradeable_even_when_unique():
    result = solve(req(
        {"a": (1, 2)}, hard=(eq("a_is_1", "a", 1),),
        original_strengths=("weak",),
    ))
    assert result.is_unique and result.confidence_upgrade_allowed is False


# 固定ケース9
def test_case_09_uncalibrated_remains_not_upgradeable():
    result = solve(req(
        {"a": (1, 2)}, hard=(eq("a_is_1", "a", 1),),
        original_calibration_states=(False,),
    ))
    assert result.is_unique and result.confidence_upgrade_allowed is False


# 固定ケース10
def test_case_10_abstained_only_cannot_be_revived_as_candidate():
    result = solve(req({"a": (0, 1)}, abstained_element_ids=("a",)))
    assert result.status == "invalid_input"
    assert result.solutions == ()


# 固定ケース11
def test_case_11_advisory_conflict_does_not_remove_hard_solution():
    result = solve(req(
        {"a": (1, 2)}, hard=(eq("hard_a_1", "a", 1),),
        advisory=(eq("advisory_a_2", "a", 2),),
    ))
    assert result.status == "unique_solution"
    assert dict(result.solutions[0])["a"] == 1


# 固定ケース12
def test_case_12_conflicting_hard_constraints_return_unsat_core():
    result = solve(req({"a": (1, 2)}, hard=(eq("a_1", "a", 1), eq("a_2", "a", 2))))
    assert result.status == "no_solution"
    assert set(result.unsat_core) >= {"a_1", "a_2"}


# 固定ケース13
def test_case_13_duplicate_candidate_values_are_not_double_counted():
    result = solve(req({"a": (1, 1, 2, 2)}))
    assert result.solution_count == 2


# 固定ケース14（空候補はinvalid_input仕様）
def test_case_14_empty_candidate_set_is_invalid():
    assert solve(req({"a": ()})).status == "invalid_input"


# 固定ケース15
def test_case_15_reversed_range_is_invalid():
    request = req({}, ranges={"a": ((5, 1),)})
    assert solve(request).status == "invalid_input"


# 固定ケース16
def test_case_16_unsupported_relation_is_not_ignored():
    bad = relation("bad", expr(a=1), "approximately", expr(constant=1))
    assert solve(req({"a": (1, 2)}, relations=(bad,))).status == "unsupported_relation"


# 固定ケース17
def test_case_17_max_solutions_is_incomplete_and_not_unique():
    result = solve(req({"a": (1, 2, 3), "b": (1, 2, 3)}, max_solutions=2))
    assert result.status == "multiple_solutions"
    assert not result.is_complete and not result.is_unique


# 固定ケース18
def test_case_18_max_combinations_exceeded_abstains():
    result = solve(req({"a": range(100), "b": range(100)}, max_combinations=100))
    assert result.status == "limit_exceeded" and result.limit_exceeded
    assert result.solutions == ()


# 固定ケース19
def test_case_19_tiny_timeout_abstains_without_unique_result():
    values = {f"v{i}": tuple(range(8)) for i in range(5)}
    result = solve(req(values, timeout_ms=1, max_combinations=100_000))
    assert result.status == "timeout" and result.timed_out
    assert not result.is_unique


# 固定ケース20
def test_case_20_same_trace_and_input_is_idempotent():
    service = ConstraintExhaustiveSearch()
    request = req({"a": (1, 2)}, hard=(eq("a1", "a", 1),), trace="same", event="same")
    first = service.search(request)
    second = service.search(request)
    assert second is first


# 難問1
def test_challenge_01_logically_unique_wrong_constraint_does_not_upgrade_confidence():
    result = solve(req({"a": (5, 9)}, hard=(eq("wrong_constraint", "a", 9),)))
    assert result.status == "unique_solution"
    assert not result.confidence_upgrade_allowed and not result.independence_added
    assert "constraint_correctness_requires_confirmation" in result.reason_codes


# 難問2
def test_challenge_02_underconstrained_problem_offers_questions_not_value():
    result = solve(req({"a": (1, 2, 3), "b": (4, 5)}))
    assert result.status == "completed_without_reduction"
    assert result.killer_question_candidates and not result.is_unique


# 難問3
def test_challenge_03_weak_true_value_does_not_override_wrong_hard_constraint():
    result = solve(req(
        {"a": (5, 9)}, hard=(eq("strong_says_9", "a", 9),),
        advisory=(eq("weak_contains_truth_5", "a", 5),),
        original_strengths=("strong", "weak"),
    ))
    assert dict(result.solutions[0])["a"] == 9
    assert "constraint_correctness_requires_confirmation" in result.reason_codes
    assert not result.confidence_upgrade_allowed


# 難問4（整合循環）
def test_challenge_04_consistent_cycle_terminates_and_solves():
    cycle = (
        relation("a_b", expr(a=1), "==", expr(b=1, constant=1)),
        relation("b_c", expr(b=1), "==", expr(c=1, constant=1)),
        relation("c_a", expr(c=1), "==", expr(a=1, constant=-2)),
    )
    result = solve(req({"a": (3, 4), "b": (2, 3), "c": (1, 2)}, relations=cycle))
    assert result.status == "multiple_solutions"


# 難問4（矛盾循環）
def test_challenge_04b_inconsistent_cycle_is_no_solution():
    cycle = (
        relation("a_b", expr(a=1), "==", expr(b=1, constant=1)),
        relation("b_c", expr(b=1), "==", expr(c=1, constant=1)),
        relation("c_a_bad", expr(c=1), "==", expr(a=1, constant=-1)),
    )
    assert solve(req({"a": (3, 4), "b": (2, 3), "c": (1, 2)}, relations=cycle)).status == "no_solution"


# 難問5
def test_challenge_05_candidate_order_does_not_change_complete_solution_set():
    constraints = (relation("sum", expr(a=1, b=1), "==", expr(constant=4)),)
    first = solve(req({"a": (1, 2, 3), "b": (1, 2, 3)}, relations=constraints, trace="order1"))
    second = solve(req({"a": (3, 2, 1), "b": (3, 1, 2)}, relations=constraints, trace="order2"))
    assert first.is_complete and second.is_complete
    assert set(first.solutions) == set(second.solutions)


def make_orchestrator():
    policies = {
        "vision": MethodPolicy(True, "strong"),
        "table": MethodPolicy(True, "strong"),
    }
    sources = {"drawing": "sha256:drawing", "spec": "sha256:spec"}
    return InferenceOrchestrator(policies, sources)


def evidence(value_range, source="drawing", method="vision", axis="image"):
    return {
        "target": "door_count", "count_range": list(value_range),
        "source_id": source, "source_fingerprint": f"sha256:{source}",
        "axis_id": axis, "method_id": method, "strength": "strong",
        "status": "confident", "calibrated": True, "derivation": "read",
        "model_confidence": 0.9, "unit": "count",
    }


def initial_payload(trace, evidence_items):
    return {"trace_id": trace, "element_id": "door", "evidence": evidence_items, "relations": []}


def event_for(orchestrator, payload):
    initial = orchestrator.process(payload)
    return initial, next(event for event in initial.events if event.failure_type == "conflicting_candidates")


# E2E-1
def test_e2e_01_unique_search_without_two_strong_sources_stays_below_tier1():
    orchestrator = make_orchestrator()
    payload = initial_payload("e2e1", [evidence((1, 3))])
    initial, event = event_for(orchestrator, payload)
    search = req({"door_count": (1, 2, 3)}, hard=(eq("door2", "door_count", 2),), trace="e2e1", event=event.event_id)
    final = orchestrator.process_with_constraint_search(payload, search)
    assert final.search_result.is_unique and final.final_confidence_tier == 3
    assert not final.auto_confirmed and final.independence_count_after == 1


# E2E-2
def test_e2e_02_existing_two_strong_sources_allow_normal_tier1_only():
    orchestrator = make_orchestrator()
    payload = initial_payload("e2e2", [
        evidence((1, 3)), evidence((1, 3), source="spec", method="table", axis="text"),
    ])
    initial, event = event_for(orchestrator, payload)
    search = req({"door_count": (1, 2, 3)}, hard=(eq("door2", "door_count", 2),), trace="e2e2", event=event.event_id)
    final = orchestrator.process_with_constraint_search(payload, search)
    assert final.search_result.is_unique and final.auto_confirmed
    assert final.independence_count_before == final.independence_count_after == 2
    assert not final.search_result.independence_added


# E2E-3
def test_e2e_03_multiple_solutions_return_to_human():
    orchestrator = make_orchestrator()
    payload = initial_payload("e2e3", [evidence((1, 3))])
    _, event = event_for(orchestrator, payload)
    final = orchestrator.process_with_constraint_search(
        payload, req({"door_count": (1, 2, 3)}, trace="e2e3", event=event.event_id)
    )
    assert final.search_result.status == "completed_without_reduction"
    assert final.human_confirmation_required and not final.auto_confirmed


# E2E-4
def test_e2e_04_no_solution_returns_constraint_recheck_without_provisional():
    orchestrator = make_orchestrator()
    payload = initial_payload("e2e4", [evidence((1, 3))])
    _, event = event_for(orchestrator, payload)
    final = orchestrator.process_with_constraint_search(
        payload, req({"door_count": (1, 2, 3)}, hard=(eq("impossible", "door_count", 9),), trace="e2e4", event=event.event_id)
    )
    assert final.search_result.status == "no_solution"
    assert final.candidate_solution is None and final.human_confirmation_required


# E2E-5
def test_e2e_05_timeout_abstains_and_returns_to_human():
    orchestrator = make_orchestrator()
    payload = initial_payload("e2e5", [evidence((1, 3))])
    _, event = event_for(orchestrator, payload)
    candidates = {f"v{i}": tuple(range(8)) for i in range(5)}
    final = orchestrator.process_with_constraint_search(
        payload, req(candidates, trace="e2e5", event=event.event_id, timeout_ms=1, max_combinations=100_000)
    )
    assert final.search_result.status == "timeout"
    assert final.candidate_solution is None and final.human_confirmation_required


def _relation_holds(item, solution):
    values = dict(solution)
    def evaluate(expression):
        return expression.constant + sum(values[name] * coefficient for name, coefficient in expression.terms)
    left, right = evaluate(item.lhs), evaluate(item.rhs)
    return {"==": left == right, "!=": left != right, "<=": left <= right,
            ">=": left >= right, "<": left < right, ">": left > right}[item.operator]


def test_randomized_properties_2000_trials():
    rng = random.Random(SEED)
    service = ConstraintExhaustiveSearch()
    for trial in range(TRIALS):
        names = tuple(f"v{i}" for i in range(rng.randint(1, 3)))
        values = {name: tuple(rng.sample(range(0, 8), rng.randint(1, 4))) for name in names}
        hard = []
        if rng.random() < 0.7:
            name = rng.choice(names)
            hard.append(eq(f"fix-{trial}", name, rng.choice(range(0, 8))))
        advisory = ()
        if rng.random() < 0.5:
            name = rng.choice(names)
            advisory = (eq(f"advisory-{trial}", name, 99),)
        max_solutions = rng.randint(1, 12)
        theoretical = 1
        for domain in values.values():
            theoretical *= len(set(domain))
        max_combinations = rng.choice([max(1, theoretical - 1), theoretical, theoretical + 10])
        timeout_ms = rng.choice([1, 10, 500])
        abstained = (rng.choice(names),) if rng.random() < 0.03 else ()
        request = req(
            values, hard=hard, advisory=advisory, trace=f"random-{trial}", event=f"event-{trial}",
            max_solutions=max_solutions, max_combinations=max_combinations,
            timeout_ms=timeout_ms,
            original_strengths=tuple(rng.choice(["strong", "weak"]) for _ in names),
            original_calibration_states=tuple(rng.choice([True, False]) for _ in names),
            abstained_element_ids=abstained,
        )
        request = ExhaustiveSearchRequest(
            **{
                **request.__dict__,
                "source_ids": tuple(
                    f"source-{rng.randint(0, 2)}" for _ in range(rng.randint(1, 4))
                ),
            }
        )
        result = service.search(request)
        assert service.search(request) is result, f"seed={SEED} trial={trial}"
        assert not result.independence_added and not result.confidence_upgrade_allowed
        assert not result.is_unique or result.is_complete
        if result.status == "timeout":
            assert result.timed_out and not result.is_unique
        if abstained:
            assert result.status == "invalid_input" and result.solutions == ()
            continue
        if result.status == "limit_exceeded":
            assert not result.solutions and not result.is_unique
            continue
        for solution in result.solutions:
            assert all(_relation_holds(item, solution) for item in hard), f"seed={SEED} trial={trial}"
        if result.status == "no_solution":
            assert result.solutions == () and result.narrowed_candidates == {}
        if result.status in {"multiple_solutions", "completed_without_reduction"}:
            assert not result.is_unique
        if not result.is_complete:
            assert result.eliminated_candidates == {}
        if result.is_complete:
            shuffled = {name: tuple(reversed(domain)) for name, domain in values.items()}
            reordered = solve(req(
                shuffled, hard=hard, advisory=advisory,
                trace=f"order-{trial}", event=f"order-event-{trial}",
                max_solutions=max_solutions, max_combinations=max_combinations,
            ))
            if reordered.is_complete:
                assert set(reordered.solutions) == set(result.solutions), f"seed={SEED} trial={trial}"
