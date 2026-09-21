"""v8 の軸品質・独立性ルールを整合性ソルバーの手前で強制する層。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from arbitration.consistency_solver import ConsistencySolver, IntRange, SolveResult

EvidenceStatus = Literal["confident", "low_confidence", "abstained"]
Strength = Literal["strong", "weak"]


@dataclass(frozen=True)
class AxisEvidence:
    """1手法の読み取りと、その独立性・品質メタデータ。"""

    target: str
    count_range: IntRange
    source_id: str
    axis_id: str
    method_id: str
    source_fingerprint: str | None = None
    strength: Strength = "strong"
    status: EvidenceStatus = "confident"
    calibrated: bool = True
    model_confidence: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lower, upper = self.count_range
        if lower > upper:
            raise ValueError(f"count_range の下限({lower})が上限({upper})を超えています")
        if not self.source_id or not self.axis_id or not self.method_id:
            raise ValueError("source_id / axis_id / method_id は空にできません")

    @property
    def evidence_id(self) -> str:
        return f"{self.source_id}::{self.axis_id}::{self.method_id}::{self.target}"

    @property
    def independence_key(self) -> str:
        """独立性は表示用IDではなく、可能なら元データの不変な指紋で判定する。"""
        return self.source_fingerprint or self.source_id

    @property
    def is_hard_eligible(self) -> bool:
        """内部confidenceではなく、強度・状態・実測校正の全条件で判定する。"""
        return (
            self.strength == "strong"
            and self.status == "confident"
            and self.calibrated
        )


@dataclass(frozen=True)
class EscalationRequest:
    failure_type: str
    conflicting_constraints: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class FirewallDecision:
    tier: int
    action: Literal["auto_confirm", "provisional_audit", "requires_review"]
    confirmed_range: IntRange | None
    independent_strong_source_count: int
    independent_advisory_source_count: int
    method_count: int
    reasons: tuple[str, ...]
    solve_result: SolveResult
    hard_evidence_ids: tuple[str, ...]
    advisory_evidence_ids: tuple[str, ...]
    abstained_evidence_ids: tuple[str, ...]
    escalation: EscalationRequest | None = None


class AxisQualityFirewall:
    """相関した手法と未校正情報を、Z3のハード制約から分離する。"""

    def assess(self, evidences: Sequence[AxisEvidence]) -> FirewallDecision:
        if not evidences:
            return self._empty_decision(method_count=0)

        targets = {item.target for item in evidences}
        if len(targets) != 1:
            raise ValueError("1回の判定では同一targetの証拠だけを渡してください")
        target = evidences[0].target
        reasons: list[str] = []

        abstained = [item for item in evidences if item.status == "abstained"]
        hard_candidates = [item for item in evidences if item.is_hard_eligible]
        advisory = [
            item
            for item in evidences
            if item.status != "abstained" and not item.is_hard_eligible
        ]

        for item in advisory:
            if not item.calibrated:
                reasons.append(
                    f"{item.method_id} は実測校正を通過していないため、内部confidenceに関係なく参考情報"
                )
            elif item.strength == "weak" or item.status == "low_confidence":
                reasons.append(f"{item.method_id} は弱い軸のためハード制約に不参加")

        by_source: dict[str, list[AxisEvidence]] = {}
        for item in evidences:
            by_source.setdefault(item.independence_key, []).append(item)
        for source_id, items in by_source.items():
            if len({item.method_id for item in items}) > 1:
                reasons.append(
                    f"同一データ源 {source_id} の複数手法は、独立軸として重複加算しない"
                )

        hard_by_source: dict[str, list[AxisEvidence]] = {}
        for item in hard_candidates:
            hard_by_source.setdefault(item.independence_key, []).append(item)

        # 同じsourceのレンズ同士が食い違う場合、そのsource全体をハードから降格する。
        usable_sources: dict[str, tuple[IntRange, list[AxisEvidence]]] = {}
        for source_id, items in hard_by_source.items():
            lower = max(item.count_range[0] for item in items)
            upper = min(item.count_range[1] for item in items)
            if lower <= upper:
                usable_sources[source_id] = ((lower, upper), items)
            else:
                advisory.extend(items)
                reasons.append(
                    f"同一データ源 {source_id} 内で手法が矛盾したため、そのsourceをハード制約から降格"
                )

        if len(usable_sources) < 2 and any(
            len({item.method_id for item in items}) > 1 for items in by_source.values()
        ):
            reasons.append("同一データ源による相関誤りの可能性があるため、自動確定しない")

        solver = ConsistencySolver()
        if usable_sources:
            all_ranges = [value[0] for value in usable_sources.values()]
            solver.add_variable(
                target,
                min(item[0] for item in all_ranges),
                max(item[1] for item in all_ranges),
                axis="quality-firewall",
            )
            for index, (source_id, (source_range, items)) in enumerate(
                sorted(usable_sources.items())
            ):
                source_var = f"{target}__source_{index}"
                solver.add_variable(
                    source_var,
                    source_range[0],
                    source_range[1],
                    axis=items[0].axis_id,
                    evidence={"source_id": source_id},
                )
                solver.add_relation(
                    f"source_link::{source_id}",
                    target,
                    "==",
                    source_var,
                    description=f"{source_id} と統合対象の一致",
                )

        solve_result = solver.solve()
        usable_items = [item for _, items in usable_sources.values() for item in items]
        hard_ids = tuple(item.evidence_id for item in usable_items)
        advisory_ids = tuple(dict.fromkeys(item.evidence_id for item in advisory))
        abstained_ids = tuple(item.evidence_id for item in abstained)
        advisory_source_count = len({item.independence_key for item in advisory})

        if solve_result.status == "unsat":
            reasons.append("独立した強い軸同士の制約が矛盾したためエスカレーション")
            return FirewallDecision(
                tier=3,
                action="requires_review",
                confirmed_range=None,
                independent_strong_source_count=len(usable_sources),
                independent_advisory_source_count=advisory_source_count,
                method_count=len(evidences),
                reasons=tuple(dict.fromkeys(reasons)),
                solve_result=solve_result,
                hard_evidence_ids=hard_ids,
                advisory_evidence_ids=advisory_ids,
                abstained_evidence_ids=abstained_ids,
                escalation=EscalationRequest(
                    failure_type="axis_contradiction",
                    conflicting_constraints=solve_result.conflicting_constraints,
                    reason="独立した強い軸の積集合が空です",
                ),
            )

        candidate_range = (
            solve_result.variables[target].solved_range
            if target in solve_result.variables
            else None
        )
        strong_count = len(usable_sources)
        if strong_count >= 2:
            tier = 1
            action = "auto_confirm"
            reasons.append("実測校正済みの独立した強いデータ源が2つ以上一致")
        elif strong_count == 1 and self._has_two_agreeing_advisory_sources(
            advisory, candidate_range
        ):
            tier = 2
            action = "provisional_audit"
            reasons.append("強い独立データ源1つを、異なる弱いデータ源2つ以上が支持")
        else:
            tier = 3
            action = "requires_review"
            reasons.append("自動確定に必要な独立した強いデータ源が2つ未満")

        return FirewallDecision(
            tier=tier,
            action=action,
            confirmed_range=candidate_range,
            independent_strong_source_count=strong_count,
            independent_advisory_source_count=advisory_source_count,
            method_count=len(evidences),
            reasons=tuple(dict.fromkeys(reasons)),
            solve_result=solve_result,
            hard_evidence_ids=hard_ids,
            advisory_evidence_ids=advisory_ids,
            abstained_evidence_ids=abstained_ids,
        )

    @staticmethod
    def _has_two_agreeing_advisory_sources(
        advisory: Sequence[AxisEvidence], candidate_range: IntRange | None
    ) -> bool:
        if candidate_range is None:
            return False
        lower, upper = candidate_range
        sources = {
            item.independence_key
            for item in advisory
            if not (item.count_range[1] < lower or item.count_range[0] > upper)
        }
        return len(sources) >= 2

    @staticmethod
    def _empty_decision(*, method_count: int) -> FirewallDecision:
        result = ConsistencySolver().solve()
        return FirewallDecision(
            tier=3,
            action="requires_review",
            confirmed_range=None,
            independent_strong_source_count=0,
            independent_advisory_source_count=0,
            method_count=method_count,
            reasons=("判定に利用できる証拠がない",),
            solve_result=result,
            hard_evidence_ids=(),
            advisory_evidence_ids=(),
            abstained_evidence_ids=(),
        )
