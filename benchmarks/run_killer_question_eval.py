"""キラークエスチョンエンジンの再現用デモ・効果測定スクリプト。

`tests/test_killer_question_engine.py`・`tests/test_killer_question_integration.py`
と同じシナリオを使い、質問の順序・回数・処理時間をまとめて表示する。
テストとして固定している数値と完全に対応しているため、報告書の数値の
再現に使う(新しい数値をここで作ってはいない)。

実行: ``python -m benchmarks.run_killer_question_eval``
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine, Question
from killer_question.firewall_bridge import add_target_to_joint_solver


def _oracle(ground_truth: dict[str, int]):
    def answer_fn(question: Question) -> int:
        return ground_truth[question.variable]

    return answer_fn


@dataclass
class ScenarioResult:
    name: str
    question_count: int
    stopped_reason: str
    seconds: float
    asked: tuple[str, ...]


def _run_and_report(name: str, solver: ConsistencySolver, ground_truth: dict[str, int]) -> ScenarioResult:
    started = time.perf_counter()
    engine = KillerQuestionEngine(solver)
    session = engine.run(_oracle(ground_truth))
    elapsed = time.perf_counter() - started
    result = ScenarioResult(
        name=name,
        question_count=session.question_count,
        stopped_reason=session.stopped_reason,
        seconds=elapsed,
        asked=tuple(a.variable for a in session.answered),
    )
    print(
        f"{result.name:32s} 質問数={result.question_count} "
        f"順序={result.asked} 終了理由={result.stopped_reason} "
        f"{result.seconds * 1000:.2f}ms"
    )
    return result


# ---------------------------------------------------------------------------
# 段階1: トライアル7〜9再現シナリオ
# ---------------------------------------------------------------------------


def star_scenario() -> tuple[ConsistencySolver, dict[str, int]]:
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="text_axis")
    for leaf in ("leaf_1", "leaf_2", "leaf_3"):
        solver.add_variable(leaf, 0, 10, axis="image_axis")
        solver.add_relation(f"{leaf}_eq_room", leaf, "==", "room_count")
    return solver, {"room_count": 4, "leaf_1": 4, "leaf_2": 4, "leaf_3": 4}


def two_independent_clusters_scenario() -> tuple[ConsistencySolver, dict[str, int]]:
    solver = ConsistencySolver()
    solver.add_variable("hub_a", 3, 5, axis="a")
    solver.add_variable("leaf_a1", 0, 10, axis="a")
    solver.add_variable("leaf_a2", 0, 10, axis="a")
    solver.add_relation("leaf_a1_eq_hub_a", "leaf_a1", "==", "hub_a")
    solver.add_relation("leaf_a2_eq_hub_a", "leaf_a2", "==", "hub_a")
    solver.add_variable("hub_b", 10, 12, axis="b")
    solver.add_variable("leaf_b1", 0, 20, axis="b")
    solver.add_relation("leaf_b1_eq_hub_b", "leaf_b1", "==", "hub_b")
    ground_truth = {"hub_a": 4, "leaf_a1": 4, "leaf_a2": 4, "hub_b": 11, "leaf_b1": 11}
    return solver, ground_truth


def chain_scenario() -> tuple[ConsistencySolver, dict[str, int]]:
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="rules")
    solver.add_variable("door_count", 0, 20, axis="image")
    solver.add_variable("window_count", 0, 20, axis="image")
    solver.add_relation("door_eq_room_plus_1", "door_count", "==", lambda v: v["room_count"] + 1)
    solver.add_relation("window_eq_door_plus_1", "window_count", "==", lambda v: v["door_count"] + 1)
    return solver, {"room_count": 4, "door_count": 5, "window_count": 6}


def confluence_scenario() -> tuple[ConsistencySolver, dict[str, int]]:
    solver = ConsistencySolver()
    solver.add_variable("door_count", 2, 6, axis="image")
    solver.add_variable("window_count", 2, 6, axis="image")
    solver.add_variable("symbol_total", 8, 8, axis="absolute_rule_axis")
    solver.add_relation(
        "total_is_door_plus_window", "symbol_total", "==",
        lambda v: v["door_count"] + v["window_count"],
    )
    return solver, {"door_count": 3, "window_count": 5}


# ---------------------------------------------------------------------------
# 段階2: axis_quality_firewall との接続シナリオ
# ---------------------------------------------------------------------------


def firewall_integration_scenario(
    door_answer: int, window_answer: int
) -> tuple[ConsistencySolver, dict[str, int]]:
    evidences_by_target = {
        "room_count": [
            AxisEvidence(target="room_count", count_range=(4, 4), source_id="drawing-A",
                         axis_id="image", method_id="room_detector", calibrated=True),
            AxisEvidence(target="room_count", count_range=(4, 4), source_id="ifc-A",
                         axis_id="rules", method_id="ifc_space_count", calibrated=True),
        ],
        "symbol_total": [
            AxisEvidence(target="symbol_total", count_range=(7, 9), source_id="ifc-A",
                         axis_id="rules", method_id="opening_count_from_wall_geometry", calibrated=True),
            AxisEvidence(target="symbol_total", count_range=(7, 9), source_id="spec-A",
                         axis_id="text", method_id="spec_sheet_estimate", calibrated=True),
        ],
        "door_count": [
            AxisEvidence(target="door_count", count_range=(2, 6), source_id="drawing-A",
                         axis_id="image", method_id="grounding_dino", calibrated=False,
                         model_confidence=0.95),
        ],
        "window_count": [
            AxisEvidence(target="window_count", count_range=(2, 6), source_id="drawing-A",
                         axis_id="image", method_id="grounding_dino", calibrated=False,
                         model_confidence=0.90),
        ],
    }
    firewall = AxisQualityFirewall()
    decisions = {target: firewall.assess(evs) for target, evs in evidences_by_target.items()}
    solver = ConsistencySolver()
    for target, evs in evidences_by_target.items():
        add_target_to_joint_solver(solver, target, decisions[target], evs)
    solver.add_relation("door_ge_room", "door_count", ">=", "room_count")
    solver.add_relation("window_ge_room", "window_count", ">=", "room_count")
    solver.add_relation(
        "total_eq_sum", "symbol_total", "==",
        lambda v: v["door_count"] + v["window_count"],
    )
    ground_truth = {
        "door_count": door_answer,
        "window_count": window_answer,
        "symbol_total": door_answer + window_answer,
    }
    return solver, ground_truth


def main() -> list[ScenarioResult]:
    print("=== 段階1: トライアル7〜9再現シナリオ ===\n")
    results = [
        _run_and_report("スター型", *star_scenario()),
        _run_and_report("独立2上流型", *two_independent_clusters_scenario()),
        _run_and_report("多段型", *chain_scenario()),
        _run_and_report("合流型", *confluence_scenario()),
    ]

    print("\n=== 段階2: axis_quality_firewall接続シナリオ ===\n")
    results.append(
        _run_and_report(
            "firewall統合(door=5, 幸運分岐)", *firewall_integration_scenario(5, 4)
        )
    )
    results.append(
        _run_and_report(
            "firewall統合(door=4, 追加質問分岐)", *firewall_integration_scenario(4, 4)
        )
    )

    return results


if __name__ == "__main__":
    main()
