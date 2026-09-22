"""エスカレーションイベントの契約と、未実装リソースへの安全な振り分け。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureType = Literal[
    "image_degraded",
    "unknown_symbol",
    "conflicting_candidates",
    "axis_contradiction",
    "statistically_unusual",
    "trade_specific_unclear",
    "insufficient_independent_evidence",
    "uncalibrated_source",
    "correlated_source_risk",
    "invalid_input",
]

ROUTE_TABLE: dict[FailureType, str] = {
    "image_degraded": "floorplan_dataset_lookup",
    "unknown_symbol": "symbol_dataset_lookup",
    "conflicting_candidates": "constraint_exhaustive_search",
    "axis_contradiction": "r1_standard_checker",
    "statistically_unusual": "mlit_statistics_checker",
    "trade_specific_unclear": "trade_rule_lookup",
    "insufficient_independent_evidence": "human_confirmation",
    "uncalibrated_source": "calibration_required",
    "correlated_source_risk": "independent_source_required",
    "invalid_input": "input_correction_required",
}


@dataclass(frozen=True)
class EscalationEvent:
    event_id: str
    element_id: str
    failure_type: FailureType
    reason_codes: tuple[str, ...]
    source_ids: tuple[str, ...]
    axis_ids: tuple[str, ...]
    method_ids: tuple[str, ...]
    conflicting_constraints: tuple[str, ...]
    unsat_core: tuple[str, ...]
    candidate_range: tuple[int, int] | None
    confidence_tier: int
    recommended_resource: str
    human_confirmation_required: bool
    created_at: str
    trace_id: str


@dataclass(frozen=True)
class RouteDecision:
    event_id: str
    failure_type: FailureType
    resource: str
    status: Literal["not_executed", "executed"] = "not_executed"
    message: str = "振り分け先のみ決定済み。深掘りリソースは未実装のため実行していません。"


class EscalationRouter:
    """failure_typeを契約済みリソース名へ写像する。実処理は行わない。"""

    def route(self, event: EscalationEvent, *, executed: bool = False) -> RouteDecision:
        resource = ROUTE_TABLE.get(event.failure_type)
        if resource is None:
            raise ValueError(f"未対応のfailure_typeです: {event.failure_type}")
        if event.failure_type == "axis_contradiction" and not event.unsat_core:
            raise ValueError("axis_contradictionには空でないunsat_coreが必要です")
        if event.recommended_resource != resource:
            raise ValueError("イベントのrecommended_resourceがルーター契約と一致しません")
        return RouteDecision(
            event_id=event.event_id,
            failure_type=event.failure_type,
            resource=resource,
            status="executed" if executed else "not_executed",
            message=(
                "制約全探索を実行しました。"
                if executed and resource == "constraint_exhaustive_search"
                else "振り分け先のみ決定済み。深掘りリソースは未実装のため実行していません。"
            ),
        )
