"""実運用推論の単一入口。

外部入力を検証・正規化し、AxisQualityFirewallだけを通してZ3へ渡す。
このモジュールはConsistencySolverを直接参照しない。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall, FirewallDecision
from arbitration.constraint_exhaustive_search import (
    ConstraintExhaustiveSearch,
    ExhaustiveSearchRequest,
    ExhaustiveSearchResult,
)
from arbitration.escalation_router import (
    ROUTE_TABLE,
    EscalationEvent,
    EscalationRouter,
    FailureType,
    RouteDecision,
)

ALLOWED_AXES = frozenset({"image", "geometry", "text", "rules", "history", "statistical"})
ALLOWED_STATUSES = frozenset({"confident", "low_confidence", "abstained"})
ALLOWED_STRENGTHS = frozenset({"strong", "weak"})
UNIT_ALIASES = {"count": "count", "件": "count", "個": "count", "数量": "count"}
MAX_COUNT = 1_000_000


@dataclass(frozen=True)
class MethodPolicy:
    """信頼された設定側が与える、手法ごとの校正状態と最大強度。"""

    calibrated: bool
    max_strength: str

    def __post_init__(self) -> None:
        if self.max_strength not in ALLOWED_STRENGTHS:
            raise ValueError("max_strengthはstrongまたはweakである必要があります")


@dataclass(frozen=True)
class OrchestrationTimings:
    total_seconds: float
    firewall_seconds: float
    z3_seconds: float
    escalation_seconds: float


@dataclass(frozen=True)
class OrchestrationResult:
    trace_id: str
    decision: FirewallDecision | None
    events: tuple[EscalationEvent, ...]
    routes: tuple[RouteDecision, ...]
    accepted_evidence_ids: tuple[str, ...]
    duplicate_evidence_count: int
    normalization_notes: tuple[str, ...]
    timings: OrchestrationTimings
    input_fingerprint: str

    @property
    def is_invalid(self) -> bool:
        return any(event.failure_type == "invalid_input" for event in self.events)


@dataclass(frozen=True)
class DeepenedOrchestrationResult:
    initial: OrchestrationResult
    search_result: ExhaustiveSearchResult | None
    deepening_route: RouteDecision | None
    final_confidence_tier: int
    auto_confirmed: bool
    candidate_solution: tuple[tuple[str, int], ...] | None
    human_confirmation_required: bool
    final_reason_codes: tuple[str, ...]
    independence_count_before: int
    independence_count_after: int


class InferenceOrchestrator:
    """Python API / JSON入力を同じ安全経路で処理する、プロセス内冪等な入口。"""

    def __init__(
        self,
        method_policies: Mapping[str, MethodPolicy] | None = None,
        source_registry: Mapping[str, str] | None = None,
    ) -> None:
        self._firewall = AxisQualityFirewall()
        self._router = EscalationRouter()
        self._constraint_search = ConstraintExhaustiveSearch()
        self._trace_cache: dict[str, OrchestrationResult] = {}
        # 未登録手法は安全側に倒し、校正済みstrongとして扱わない。
        self._method_policies = dict(method_policies or {})
        # source_idと元データ指紋の対応は信頼された設定からのみ受け取る。
        self._source_registry = dict(source_registry or {})

    def process_with_constraint_search(
        self,
        request: Mapping[str, Any],
        search_request: ExhaustiveSearchRequest,
    ) -> DeepenedOrchestrationResult:
        """通常判定後のconflicting_candidatesだけを全探索へ渡し、証拠は追加しない。"""
        initial = self.process(request)
        decision = initial.decision
        before = decision.independent_strong_source_count if decision else 0
        event = next(
            (item for item in initial.events if item.failure_type == "conflicting_candidates"),
            None,
        )
        if event is None:
            return DeepenedOrchestrationResult(
                initial=initial, search_result=None, deepening_route=None,
                final_confidence_tier=decision.tier if decision else 3,
                auto_confirmed=bool(decision and decision.action == "auto_confirm"),
                candidate_solution=None,
                human_confirmation_required=not bool(decision and decision.action == "auto_confirm"),
                final_reason_codes=("constraint_search_not_required",),
                independence_count_before=before, independence_count_after=before,
            )
        if search_request.trace_id != initial.trace_id or search_request.event_id != event.event_id:
            invalid = ExhaustiveSearchResult(
                status="invalid_input", solution_count=0, solutions=(), narrowed_candidates={},
                eliminated_candidates={}, elimination_reasons={}, is_unique=False,
                is_complete=False, timed_out=False, limit_exceeded=False, unsat_core=(),
                independence_added=False, confidence_upgrade_allowed=False,
                human_confirmation_required=True, reason_codes=("event_contract_mismatch",),
                elapsed_ms=0.0, theoretical_combinations=0,
            )
            return DeepenedOrchestrationResult(
                initial=initial, search_result=invalid, deepening_route=None,
                final_confidence_tier=decision.tier if decision else 3,
                auto_confirmed=False, candidate_solution=None,
                human_confirmation_required=True,
                final_reason_codes=invalid.reason_codes,
                independence_count_before=before, independence_count_after=before,
            )

        search_result = self._constraint_search.search(search_request)
        route = self._router.route(event, executed=True)
        normal_auto_confirm = bool(decision and decision.action == "auto_confirm")
        auto_confirmed = normal_auto_confirm and search_result.is_unique
        candidate = search_result.solutions[0] if search_result.is_unique else None
        reasons = list(search_result.reason_codes)
        if search_result.status == "no_solution":
            reasons.append("axis_contradiction_or_constraint_recheck_required")
        elif search_result.status in {"multiple_solutions", "completed_without_reduction"}:
            reasons.append("human_or_killer_question_required")
        elif search_result.status in {"timeout", "limit_exceeded"}:
            reasons.append("deepening_abstained_safely")
        if search_result.is_unique and not normal_auto_confirm:
            reasons.append("logical_uniqueness_does_not_upgrade_confidence_tier")
        return DeepenedOrchestrationResult(
            initial=initial,
            search_result=search_result,
            deepening_route=route,
            final_confidence_tier=decision.tier if decision else 3,
            auto_confirmed=auto_confirmed,
            candidate_solution=candidate,
            human_confirmation_required=not auto_confirmed,
            final_reason_codes=tuple(dict.fromkeys(reasons)),
            independence_count_before=before,
            independence_count_after=before,
        )

    def process_json(self, payload: str | bytes | bytearray) -> OrchestrationResult:
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError, UnicodeError) as exc:
            return self._invalid_result(
                trace_id="invalid-json",
                element_id="unknown",
                reason_codes=("broken_json", type(exc).__name__),
                fingerprint=self._fingerprint_raw(payload),
            )
        if not isinstance(decoded, dict):
            return self._invalid_result(
                trace_id="invalid-json",
                element_id="unknown",
                reason_codes=("json_root_must_be_object",),
                fingerprint=self._fingerprint_raw(payload),
            )
        return self.process(decoded)

    def process(self, request: Mapping[str, Any]) -> OrchestrationResult:
        started = time.perf_counter()
        if not isinstance(request, Mapping):
            return self._invalid_result(
                trace_id="invalid-request",
                element_id="unknown",
                reason_codes=("request_must_be_mapping",),
                fingerprint=self._fingerprint_raw(repr(request)),
                started=started,
            )

        trace_id = self._required_text(request.get("trace_id"))
        element_id = self._required_text(request.get("element_id"))
        fingerprint = self._fingerprint_mapping(request)
        if trace_id and trace_id in self._trace_cache:
            cached = self._trace_cache[trace_id]
            if cached.input_fingerprint == fingerprint:
                return cached
            return self._invalid_result(
                trace_id=trace_id,
                element_id=element_id or "unknown",
                reason_codes=("trace_id_payload_mismatch",),
                fingerprint=fingerprint,
                started=started,
            )

        base_errors: list[str] = []
        if trace_id is None:
            base_errors.append("missing_trace_id")
        if element_id is None:
            base_errors.append("missing_element_id")
        raw_evidence = request.get("evidence")
        if not isinstance(raw_evidence, list):
            base_errors.append("evidence_must_be_list")
            raw_evidence = []
        relations = request.get("relations", [])
        if not isinstance(relations, list):
            base_errors.append("relations_must_be_list")
        elif relations:
            # 外部relationの安全な名前解決・循環検査は未実装。黙って採用しない。
            base_errors.append("external_relations_not_supported")

        if base_errors:
            result = self._invalid_result(
                trace_id=trace_id or "invalid-request",
                element_id=element_id or "unknown",
                reason_codes=tuple(base_errors),
                fingerprint=fingerprint,
                started=started,
            )
            if trace_id:
                self._trace_cache.setdefault(trace_id, result)
            return result

        normalized: list[AxisEvidence] = []
        notes: list[str] = []
        errors: list[str] = []
        duplicate_count = 0
        seen: dict[str, AxisEvidence] = {}
        units: set[str] = set()

        for index, raw in enumerate(raw_evidence):
            parsed, item_notes, item_errors, unit = self._parse_evidence(raw, index)
            notes.extend(item_notes)
            errors.extend(item_errors)
            if unit:
                units.add(unit)
            if parsed is None:
                continue
            previous = seen.get(parsed.evidence_id)
            if previous is not None:
                if previous == parsed:
                    duplicate_count += 1
                    notes.append(f"duplicate_evidence_removed:{parsed.evidence_id}")
                else:
                    errors.append(f"conflicting_duplicate:{parsed.evidence_id}")
                continue
            seen[parsed.evidence_id] = parsed
            normalized.append(parsed)

        if len(units) > 1:
            errors.append("unit_mismatch")
        if not normalized and not errors:
            errors.append("empty_evidence")
        if errors:
            result = self._invalid_result(
                trace_id=trace_id,
                element_id=element_id,
                reason_codes=tuple(dict.fromkeys(errors)),
                fingerprint=fingerprint,
                source_ids=tuple(sorted({item.source_id for item in normalized})),
                axis_ids=tuple(sorted({item.axis_id for item in normalized})),
                method_ids=tuple(sorted({item.method_id for item in normalized})),
                duplicate_count=duplicate_count,
                notes=tuple(notes),
                started=started,
            )
            self._trace_cache[trace_id] = result
            return result

        firewall_started = time.perf_counter()
        decision = self._firewall.assess(normalized)
        firewall_seconds = time.perf_counter() - firewall_started

        escalation_started = time.perf_counter()
        events = self._events_for_decision(
            trace_id=trace_id,
            element_id=element_id,
            evidences=normalized,
            decision=decision,
        )
        routes = tuple(self._router.route(event) for event in events)
        escalation_seconds = time.perf_counter() - escalation_started
        total_seconds = time.perf_counter() - started
        result = OrchestrationResult(
            trace_id=trace_id,
            decision=decision,
            events=events,
            routes=routes,
            accepted_evidence_ids=tuple(item.evidence_id for item in normalized),
            duplicate_evidence_count=duplicate_count,
            normalization_notes=tuple(notes),
            timings=OrchestrationTimings(
                total_seconds=total_seconds,
                firewall_seconds=firewall_seconds,
                z3_seconds=decision.solve_result.solve_seconds,
                escalation_seconds=escalation_seconds,
            ),
            input_fingerprint=fingerprint,
        )
        self._trace_cache[trace_id] = result
        return result

    def _parse_evidence(
        self, raw: Any, index: int
    ) -> tuple[AxisEvidence | None, list[str], list[str], str | None]:
        notes: list[str] = []
        errors: list[str] = []
        prefix = f"evidence[{index}]"
        if not isinstance(raw, Mapping):
            return None, notes, [f"{prefix}:not_object"], None

        required = {
            name: self._required_text(raw.get(name))
            for name in ("target", "source_id", "source_fingerprint", "axis_id", "method_id")
        }
        for name, value in required.items():
            if value is None:
                errors.append(f"{prefix}:missing_{name}")

        source_id = required["source_id"]
        source_fingerprint = required["source_fingerprint"]
        registered_fingerprint = self._source_registry.get(source_id) if source_id else None
        if source_id and registered_fingerprint is None:
            errors.append(f"{prefix}:unregistered_source")
        elif registered_fingerprint is not None and source_fingerprint != registered_fingerprint:
            errors.append(f"{prefix}:source_fingerprint_mismatch")

        axis_id = required["axis_id"]
        if axis_id and axis_id not in ALLOWED_AXES:
            errors.append(f"{prefix}:unknown_axis_id")
        status = raw.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}:invalid_status")
        strength = raw.get("strength")
        if strength not in ALLOWED_STRENGTHS:
            errors.append(f"{prefix}:invalid_strength")
        calibrated = raw.get("calibrated")
        if not isinstance(calibrated, bool):
            errors.append(f"{prefix}:calibration_must_be_explicit_boolean")
            calibrated = False

        method_id = required["method_id"]
        policy = self._method_policies.get(method_id) if method_id else None
        if policy is None:
            notes.append(f"unregistered_method_downgraded:{method_id or 'unknown'}")
            effective_calibrated = False
            effective_strength = "weak"
        else:
            effective_calibrated = bool(calibrated and policy.calibrated)
            effective_strength = (
                "strong"
                if strength == "strong" and policy.max_strength == "strong"
                else "weak"
            )
            if calibrated and not policy.calibrated:
                notes.append(f"untrusted_calibration_ignored:{method_id}")
            if strength == "strong" and policy.max_strength != "strong":
                notes.append(f"strength_downgraded_by_policy:{method_id}")

        confidence = raw.get("model_confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                errors.append(f"{prefix}:invalid_model_confidence")
            elif not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
                errors.append(f"{prefix}:invalid_model_confidence")

        unit_raw = self._required_text(raw.get("unit"))
        unit = UNIT_ALIASES.get(unit_raw) if unit_raw else None
        if unit_raw is None:
            errors.append(f"{prefix}:missing_unit")
        elif unit is None:
            errors.append(f"{prefix}:unknown_unit")
        elif unit_raw != unit:
            notes.append(f"unit_normalized:{unit_raw}->{unit}")

        count_range = raw.get("count_range")
        parsed_range: tuple[int, int] | None = None
        if not isinstance(count_range, (list, tuple)) or len(count_range) != 2:
            errors.append(f"{prefix}:invalid_count_range")
        else:
            lower, upper = count_range
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (lower, upper)):
                errors.append(f"{prefix}:count_must_be_integer")
            elif lower < 0 or upper < 0:
                errors.append(f"{prefix}:negative_count")
            elif lower > upper:
                errors.append(f"{prefix}:reversed_range")
            elif upper > MAX_COUNT:
                errors.append(f"{prefix}:count_too_large")
            else:
                parsed_range = (lower, upper)

        if errors:
            return None, notes, errors, unit
        assert parsed_range is not None
        return (
            AxisEvidence(
                target=required["target"],
                count_range=parsed_range,
                source_id=source_id,
                axis_id=axis_id,
                method_id=required["method_id"],
                source_fingerprint=registered_fingerprint,
                strength=effective_strength,
                status=status,
                calibrated=effective_calibrated,
                model_confidence=float(confidence) if confidence is not None else None,
                evidence={"unit": unit},
            ),
            notes,
            errors,
            unit,
        )

    def _events_for_decision(
        self,
        *,
        trace_id: str,
        element_id: str,
        evidences: Sequence[AxisEvidence],
        decision: FirewallDecision,
    ) -> tuple[EscalationEvent, ...]:
        failures: list[tuple[FailureType, tuple[str, ...]]] = []
        if decision.solve_result.status == "unsat":
            failures.append(("axis_contradiction", ("independent_strong_axes_unsat",)))
        if any(
            item.is_hard_eligible and item.count_range[0] < item.count_range[1]
            for item in evidences
        ):
            failures.append(("conflicting_candidates", ("finite_candidate_range_requires_deepening",)))
        if any(not item.calibrated and item.status != "abstained" for item in evidences):
            failures.append(("uncalibrated_source", ("empirical_calibration_missing_or_failed",)))
        if any("相関誤り" in reason for reason in decision.reasons):
            failures.append(("correlated_source_risk", ("same_source_multiple_methods",)))
        if decision.tier == 3 and decision.solve_result.status != "unsat":
            failures.append(("insufficient_independent_evidence", ("tier1_requirements_not_met",)))

        return tuple(
            self._make_event(
                trace_id=trace_id,
                element_id=element_id,
                failure_type=failure_type,
                reason_codes=reason_codes,
                evidences=evidences,
                decision=decision,
            )
            for failure_type, reason_codes in dict.fromkeys(failures)
        )

    def _invalid_result(
        self,
        *,
        trace_id: str,
        element_id: str,
        reason_codes: tuple[str, ...],
        fingerprint: str,
        source_ids: tuple[str, ...] = (),
        axis_ids: tuple[str, ...] = (),
        method_ids: tuple[str, ...] = (),
        duplicate_count: int = 0,
        notes: tuple[str, ...] = (),
        started: float | None = None,
    ) -> OrchestrationResult:
        event = self._make_standalone_event(
            trace_id=trace_id,
            element_id=element_id,
            failure_type="invalid_input",
            reason_codes=reason_codes,
            source_ids=source_ids,
            axis_ids=axis_ids,
            method_ids=method_ids,
        )
        escalation_started = time.perf_counter()
        route = self._router.route(event)
        escalation_seconds = time.perf_counter() - escalation_started
        total = time.perf_counter() - started if started is not None else 0.0
        return OrchestrationResult(
            trace_id=trace_id,
            decision=None,
            events=(event,),
            routes=(route,),
            accepted_evidence_ids=(),
            duplicate_evidence_count=duplicate_count,
            normalization_notes=notes,
            timings=OrchestrationTimings(total, 0.0, 0.0, escalation_seconds),
            input_fingerprint=fingerprint,
        )

    def _make_event(
        self,
        *,
        trace_id: str,
        element_id: str,
        failure_type: FailureType,
        reason_codes: tuple[str, ...],
        evidences: Sequence[AxisEvidence],
        decision: FirewallDecision,
    ) -> EscalationEvent:
        return self._make_standalone_event(
            trace_id=trace_id,
            element_id=element_id,
            failure_type=failure_type,
            reason_codes=reason_codes,
            source_ids=tuple(sorted({item.source_id for item in evidences})),
            axis_ids=tuple(sorted({item.axis_id for item in evidences})),
            method_ids=tuple(sorted({item.method_id for item in evidences})),
            conflicting_constraints=decision.solve_result.conflicting_constraints,
            unsat_core=decision.solve_result.conflicting_constraints,
            candidate_range=decision.confirmed_range,
            confidence_tier=decision.tier,
        )

    @staticmethod
    def _make_standalone_event(
        *,
        trace_id: str,
        element_id: str,
        failure_type: FailureType,
        reason_codes: tuple[str, ...],
        source_ids: tuple[str, ...] = (),
        axis_ids: tuple[str, ...] = (),
        method_ids: tuple[str, ...] = (),
        conflicting_constraints: tuple[str, ...] = (),
        unsat_core: tuple[str, ...] = (),
        candidate_range: tuple[int, int] | None = None,
        confidence_tier: int = 3,
    ) -> EscalationEvent:
        event_key = "|".join((trace_id, element_id, failure_type, *reason_codes))
        return EscalationEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, event_key)),
            element_id=element_id,
            failure_type=failure_type,
            reason_codes=reason_codes,
            source_ids=source_ids,
            axis_ids=axis_ids,
            method_ids=method_ids,
            conflicting_constraints=conflicting_constraints,
            unsat_core=unsat_core,
            candidate_range=candidate_range,
            confidence_tier=confidence_tier,
            recommended_resource=ROUTE_TABLE[failure_type],
            human_confirmation_required=True,
            created_at=datetime.now(timezone.utc).isoformat(),
            trace_id=trace_id,
        )

    @staticmethod
    def _required_text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _fingerprint_mapping(value: Mapping[str, Any]) -> str:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            encoded = repr(value)
        return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _fingerprint_raw(value: Any) -> str:
        if isinstance(value, bytes):
            raw = value
        elif isinstance(value, bytearray):
            raw = bytes(value)
        else:
            raw = str(value).encode("utf-8", errors="replace")
        return hashlib.sha256(raw).hexdigest()
