"""拮抗候補を既存制約で列挙・枝刈りするZ3深掘りリソース。"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Literal, Mapping, Sequence

import z3

SearchStatus = Literal[
    "unique_solution",
    "multiple_solutions",
    "no_solution",
    "timeout",
    "limit_exceeded",
    "invalid_input",
    "unsupported_relation",
    "completed_without_reduction",
]

SUPPORTED_OPERATORS = frozenset({"==", "!=", "<=", ">=", "<", ">"})
_OPS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    "<=": lambda left, right: left <= right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    ">": lambda left, right: left > right,
}


@dataclass(frozen=True)
class LinearExpression:
    terms: tuple[tuple[str, int], ...] = ()
    constant: int = 0


@dataclass(frozen=True)
class SearchRelation:
    name: str
    lhs: LinearExpression
    operator: str
    rhs: LinearExpression


@dataclass(frozen=True)
class ExhaustiveSearchRequest:
    trace_id: str
    event_id: str
    element_ids: tuple[str, ...]
    candidate_values: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    candidate_ranges: Mapping[str, tuple[tuple[int, int], ...]] = field(default_factory=dict)
    relations: tuple[SearchRelation, ...] = ()
    hard_constraints: tuple[SearchRelation, ...] = ()
    advisory_information: tuple[SearchRelation, ...] = ()
    source_ids: tuple[str, ...] = ()
    axis_ids: tuple[str, ...] = ()
    method_ids: tuple[str, ...] = ()
    original_confidence_tiers: tuple[int, ...] = ()
    original_strengths: tuple[str, ...] = ()
    original_calibration_states: tuple[bool, ...] = ()
    abstained_element_ids: tuple[str, ...] = ()
    max_solutions: int = 100
    max_combinations: int = 100_000
    timeout_ms: int = 1_000


@dataclass(frozen=True)
class ExhaustiveSearchResult:
    status: SearchStatus
    solution_count: int
    solutions: tuple[tuple[tuple[str, int], ...], ...]
    narrowed_candidates: Mapping[str, tuple[int, ...]]
    eliminated_candidates: Mapping[str, tuple[int, ...]]
    elimination_reasons: Mapping[str, Mapping[int, str]]
    is_unique: bool
    is_complete: bool
    timed_out: bool
    limit_exceeded: bool
    unsat_core: tuple[str, ...]
    independence_added: bool
    confidence_upgrade_allowed: bool
    human_confirmation_required: bool
    reason_codes: tuple[str, ...]
    elapsed_ms: float
    theoretical_combinations: int
    killer_question_candidates: tuple[str, ...] = ()


class ConstraintExhaustiveSearch:
    """有限候補をZ3で列挙する。新しい証拠・独立軸は生成しない。"""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[str, ExhaustiveSearchResult]] = {}

    def search(self, request: ExhaustiveSearchRequest) -> ExhaustiveSearchResult:
        started = time.perf_counter()
        fingerprint = self._fingerprint(request)
        cache_key = (request.trace_id, request.event_id)
        cached = self._cache.get(cache_key)
        if cached:
            if cached[0] == fingerprint:
                return cached[1]
            return self._terminal(
                "invalid_input", started, reason_codes=("idempotency_payload_mismatch",)
            )

        validation = self._validate(request)
        if validation:
            status, reason_codes = validation
            result = self._terminal(status, started, reason_codes=reason_codes)
            self._cache[cache_key] = (fingerprint, result)
            return result

        theoretical = self._theoretical_combinations(request)
        if theoretical > request.max_combinations:
            result = self._terminal(
                "limit_exceeded",
                started,
                reason_codes=("max_combinations_exceeded",),
                theoretical_combinations=theoretical,
                limit_exceeded=True,
            )
            self._cache[cache_key] = (fingerprint, result)
            return result
        # 1ms以下では大きな問題を開始しても完了性を保証できないため、事前棄権する。
        if request.timeout_ms <= 1 and theoretical > 1_000:
            result = self._terminal(
                "timeout", started,
                reason_codes=("timeout_budget_below_safe_start_threshold",),
                theoretical_combinations=theoretical,
            )
            self._cache[cache_key] = (fingerprint, result)
            return result
        domains = self._build_domains(request)

        variables = {name: z3.Int(name) for name in request.element_ids}
        solver = z3.Solver()
        solver.set(timeout=request.timeout_ms)
        for name, values in domains.items():
            solver.assert_and_track(
                z3.Or(*(variables[name] == value for value in values)),
                f"domain::{name}",
            )
        all_hard = request.relations + request.hard_constraints
        for relation in all_hard:
            solver.assert_and_track(self._relation_expr(relation, variables), relation.name)

        solutions: list[tuple[tuple[str, int], ...]] = []
        complete = False
        timed_out = False
        initial_unsat_core: tuple[str, ...] = ()
        while len(solutions) < request.max_solutions:
            elapsed_ms = (time.perf_counter() - started) * 1000
            remaining_ms = request.timeout_ms - elapsed_ms
            if remaining_ms <= 0:
                timed_out = True
                break
            solver.set(timeout=max(1, math.ceil(remaining_ms)))
            status = solver.check()
            if status == z3.unknown:
                timed_out = True
                break
            if status == z3.unsat:
                if not solutions:
                    initial_unsat_core = tuple(sorted(str(item) for item in solver.unsat_core()))
                complete = True
                break
            model = solver.model()
            solution = tuple(
                (name, model.eval(variables[name]).as_long())
                for name in sorted(variables)
            )
            if solution not in solutions:
                solutions.append(solution)
            solver.add(z3.Or(*(variables[name] != value for name, value in solution)))

        if not complete and not timed_out and len(solutions) >= request.max_solutions:
            elapsed_ms = (time.perf_counter() - started) * 1000
            remaining_ms = request.timeout_ms - elapsed_ms
            if remaining_ms <= 0:
                timed_out = True
            else:
                solver.set(timeout=max(1, math.ceil(remaining_ms)))
                extra = solver.check()
                if extra == z3.unsat:
                    complete = True
                elif extra == z3.unknown:
                    timed_out = True

        if timed_out:
            result = self._result(
                "timeout", started, domains, solutions,
                is_complete=False, timed_out=True, theoretical=theoretical,
                reason_codes=("timeout_before_complete_enumeration",),
            )
        elif not solutions:
            result = self._result(
                "no_solution", started, domains, solutions,
                is_complete=True, theoretical=theoretical, unsat_core=initial_unsat_core,
                reason_codes=("no_hard_constraint_solution", "recheck_strong_evidence_or_constraints"),
            )
        elif not complete:
            result = self._result(
                "multiple_solutions", started, domains, solutions,
                is_complete=False, theoretical=theoretical,
                reason_codes=("max_solutions_reached", "representative_value_not_selected"),
            )
        else:
            used = {name: {dict(solution)[name] for solution in solutions} for name in domains}
            reduction = any(len(used[name]) < len(values) for name, values in domains.items())
            if len(solutions) == 1:
                status = "unique_solution"
                reasons = (
                    "logical_unique_if_constraints_are_correct",
                    "constraint_correctness_requires_confirmation",
                )
            elif not reduction and len(solutions) == theoretical:
                status = "completed_without_reduction"
                reasons = ("constraints_did_not_reduce_candidates", "representative_value_not_selected")
            else:
                status = "multiple_solutions"
                reasons = ("multiple_valid_solutions_remain", "representative_value_not_selected")
            result = self._result(
                status, started, domains, solutions,
                is_complete=True, theoretical=theoretical, reason_codes=reasons,
            )

        self._cache[cache_key] = (fingerprint, result)
        return result

    @staticmethod
    def _validate(
        request: ExhaustiveSearchRequest,
    ) -> tuple[SearchStatus, tuple[str, ...]] | None:
        if not request.trace_id or not request.event_id:
            return "invalid_input", ("missing_trace_or_event_id",)
        if not request.element_ids or len(set(request.element_ids)) != len(request.element_ids):
            return "invalid_input", ("empty_or_duplicate_element_ids",)
        if request.max_solutions < 1 or request.max_combinations < 1 or request.timeout_ms < 1:
            return "invalid_input", ("invalid_safety_limit",)
        if set(request.abstained_element_ids) & (
            set(request.candidate_values) | set(request.candidate_ranges)
        ):
            return "invalid_input", ("abstained_candidate_revival_forbidden",)
        known = set(request.element_ids)
        if set(request.candidate_values) | set(request.candidate_ranges) != known:
            return "invalid_input", ("candidate_domain_mismatch",)
        if set(request.candidate_values) & set(request.candidate_ranges):
            return "invalid_input", ("values_and_ranges_both_given",)
        for name, values in request.candidate_values.items():
            if not values:
                return "invalid_input", (f"empty_candidates::{name}",)
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
                return "invalid_input", (f"invalid_candidate::{name}",)
        for name, ranges in request.candidate_ranges.items():
            if not ranges:
                return "invalid_input", (f"empty_ranges::{name}",)
            for lower, upper in ranges:
                if lower < 0 or upper < 0 or lower > upper:
                    return "invalid_input", (f"invalid_range::{name}",)
        relation_names: set[str] = set()
        for relation in request.relations + request.hard_constraints:
            if relation.operator not in SUPPORTED_OPERATORS:
                return "unsupported_relation", (f"unsupported_operator::{relation.operator}",)
            if not relation.name or relation.name in relation_names:
                return "invalid_input", ("empty_or_duplicate_relation_name",)
            relation_names.add(relation.name)
            referenced = {name for name, _ in relation.lhs.terms + relation.rhs.terms}
            if not referenced <= known:
                return "invalid_input", (f"unknown_relation_variable::{relation.name}",)
        return None

    @staticmethod
    def _theoretical_combinations(request: ExhaustiveSearchRequest) -> int:
        theoretical = 1
        for name in request.element_ids:
            if name in request.candidate_values:
                size = len(set(request.candidate_values[name]))
            else:
                merged: list[list[int]] = []
                for lower, upper in sorted(request.candidate_ranges[name]):
                    if merged and lower <= merged[-1][1] + 1:
                        merged[-1][1] = max(merged[-1][1], upper)
                    else:
                        merged.append([lower, upper])
                size = sum(upper - lower + 1 for lower, upper in merged)
            theoretical *= size
        return theoretical

    @staticmethod
    def _build_domains(
        request: ExhaustiveSearchRequest,
    ) -> dict[str, tuple[int, ...]]:
        domains: dict[str, tuple[int, ...]] = {}
        for name in request.element_ids:
            if name in request.candidate_values:
                values = tuple(sorted(set(request.candidate_values[name])))
            else:
                values_set: set[int] = set()
                for lower, upper in request.candidate_ranges[name]:
                    values_set.update(range(lower, upper + 1))
                values = tuple(sorted(values_set))
            domains[name] = values
        return domains

    @staticmethod
    def _relation_expr(
        relation: SearchRelation, variables: Mapping[str, z3.ArithRef]
    ) -> z3.BoolRef:
        def build(expression: LinearExpression):
            return expression.constant + z3.Sum(
                *(variables[name] * coefficient for name, coefficient in expression.terms)
            )

        return _OPS[relation.operator](build(relation.lhs), build(relation.rhs))

    @classmethod
    def _result(
        cls,
        status: SearchStatus,
        started: float,
        domains: Mapping[str, tuple[int, ...]],
        solutions: Sequence[tuple[tuple[str, int], ...]],
        *,
        is_complete: bool,
        theoretical: int,
        timed_out: bool = False,
        limit_exceeded: bool = False,
        unsat_core: tuple[str, ...] = (),
        reason_codes: tuple[str, ...] = (),
    ) -> ExhaustiveSearchResult:
        used = {
            name: tuple(sorted({dict(solution)[name] for solution in solutions}))
            for name in domains
        }
        narrowed = used if is_complete and solutions else {}
        eliminated = {
            name: tuple(value for value in domain if value not in set(used[name]))
            for name, domain in domains.items()
        } if is_complete and solutions else {}
        elimination_reasons = {
            name: {value: "excluded_by_hard_constraints" for value in values}
            for name, values in eliminated.items() if values
        }
        questions = ()
        if status in {"multiple_solutions", "completed_without_reduction"}:
            varying = [name for name, values in used.items() if len(values) > 1]
            questions = tuple(f"{name} の候補を確認してください" for name in varying[:3])
        return ExhaustiveSearchResult(
            status=status,
            solution_count=len(solutions),
            solutions=tuple(sorted(set(solutions))),
            narrowed_candidates=narrowed,
            eliminated_candidates=eliminated,
            elimination_reasons=elimination_reasons,
            is_unique=status == "unique_solution" and is_complete,
            is_complete=is_complete,
            timed_out=timed_out,
            limit_exceeded=limit_exceeded,
            unsat_core=unsat_core,
            independence_added=False,
            confidence_upgrade_allowed=False,
            human_confirmation_required=True,
            reason_codes=reason_codes + (("advisory_not_used_as_hard_constraint",) if status not in {"invalid_input", "unsupported_relation"} else ()),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            theoretical_combinations=theoretical,
            killer_question_candidates=questions,
        )

    @classmethod
    def _terminal(
        cls,
        status: SearchStatus,
        started: float,
        *,
        reason_codes: tuple[str, ...],
        theoretical_combinations: int = 0,
        limit_exceeded: bool = False,
    ) -> ExhaustiveSearchResult:
        return ExhaustiveSearchResult(
            status=status, solution_count=0, solutions=(), narrowed_candidates={},
            eliminated_candidates={}, elimination_reasons={}, is_unique=False,
            is_complete=False, timed_out=status == "timeout",
            limit_exceeded=limit_exceeded, unsat_core=(), independence_added=False,
            confidence_upgrade_allowed=False, human_confirmation_required=True,
            reason_codes=reason_codes, elapsed_ms=(time.perf_counter() - started) * 1000,
            theoretical_combinations=theoretical_combinations,
        )

    @staticmethod
    def _fingerprint(request: ExhaustiveSearchRequest) -> str:
        encoded = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
