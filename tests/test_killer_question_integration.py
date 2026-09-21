"""段階2: 既存基盤(axis_quality_firewall・inference_orchestrator)との接続確認。

トライアル7〜9の概念実証は統制された合成データの上で行われていた。ここでは、
実際に ``AxisQualityFirewall`` が処理する現実的な軸データ(強い軸/弱い軸の
区別、確信度ステータス、校正の有無を含む)を入力として使い、キラー
クエスチョンが正しく選定・伝播されるかを確認する。これが段階1と唯一異なる、
新しい確認事項である(段階1は ``ConsistencySolver`` を直接組み立てていたが、
ここでは ``AxisQualityFirewall.assess()`` を経由した結果から組み立てる)。

シナリオ
--------
- room_count: 独立した2つの強い軸(画像軸の部屋検出、IfcOpenShellの空間数)が
  ぴったり一致 → 階層1・自動確定
- symbol_total(戸+窓の合計): 独立した2つの強い軸(壁の開口部からの推定、
  仕様書の記載)が(7, 9)で一致 → 階層1・自動確定(ただし単一値ではなく
  幅のある範囲での確定である点に注意)
- door_count / window_count: Grounding DINOの読み取りのみ。
  ``docs/design_v8.md`` 11章の決定により ``calibrated=False`` として登録
  している(内部の ``model_confidence`` が高くても校正済みとは扱わない)。
  そのため両方とも階層3・要確認になる

この4要素を ``killer_question/firewall_bridge.py`` で1つの結合solverへ
組み直し、「戸+窓の合計」「開き戸数・窓数はいずれも部屋数以上」という
絶対ルール軸相当の関係式を追加した上でキラークエスチョンエンジンを走らせる。
"""

from __future__ import annotations

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall, FirewallDecision
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine
from killer_question.firewall_bridge import FIREWALL_CONFIRMED_AXIS, add_target_to_joint_solver


def _evidence_scenario() -> dict[str, list[AxisEvidence]]:
    return {
        "room_count": [
            AxisEvidence(
                target="room_count", count_range=(4, 4), source_id="drawing-A",
                axis_id="image", method_id="room_detector", calibrated=True,
            ),
            AxisEvidence(
                target="room_count", count_range=(4, 4), source_id="ifc-A",
                axis_id="rules", method_id="ifc_space_count", calibrated=True,
            ),
        ],
        "symbol_total": [
            AxisEvidence(
                target="symbol_total", count_range=(7, 9), source_id="ifc-A",
                axis_id="rules", method_id="opening_count_from_wall_geometry", calibrated=True,
            ),
            AxisEvidence(
                target="symbol_total", count_range=(7, 9), source_id="spec-A",
                axis_id="text", method_id="spec_sheet_estimate", calibrated=True,
            ),
        ],
        "door_count": [
            AxisEvidence(
                target="door_count", count_range=(2, 6), source_id="drawing-A",
                axis_id="image", method_id="grounding_dino",
                calibrated=False,  # docs/design_v8.md 11章の決定
                model_confidence=0.95,
            ),
        ],
        "window_count": [
            AxisEvidence(
                target="window_count", count_range=(2, 6), source_id="drawing-A",
                axis_id="image", method_id="grounding_dino",
                calibrated=False,
                model_confidence=0.90,
            ),
        ],
    }


def _run_firewall(evidences_by_target: dict[str, list[AxisEvidence]]) -> dict[str, FirewallDecision]:
    firewall = AxisQualityFirewall()
    return {target: firewall.assess(evs) for target, evs in evidences_by_target.items()}


def _build_joint_solver(
    evidences_by_target: dict[str, list[AxisEvidence]],
    decisions: dict[str, FirewallDecision],
) -> ConsistencySolver:
    solver = ConsistencySolver()
    for target, evidences in evidences_by_target.items():
        add_target_to_joint_solver(solver, target, decisions[target], evidences)
    solver.add_relation(
        "door_ge_room", "door_count", ">=", "room_count",
        description="IfcOpenShell space_without_door 相当",
    )
    solver.add_relation(
        "window_ge_room", "window_count", ">=", "room_count",
        description="IfcOpenShell space_without_window 相当",
    )
    solver.add_relation(
        "total_eq_sum", "symbol_total", "==",
        lambda v: v["door_count"] + v["window_count"],
        description="戸+窓の合計=既知の開口部総数",
    )
    return solver


def test_firewall_tiers_match_the_calibration_policy() -> None:
    """room_count/symbol_totalは独立2強軸で階層1、door/windowはGrounding DINOの
    calibrated=False(design_v8.md 11章)のため階層3になることを確認する。
    """
    evidences_by_target = _evidence_scenario()
    decisions = _run_firewall(evidences_by_target)

    assert decisions["room_count"].tier == 1
    assert decisions["room_count"].action == "auto_confirm"
    assert decisions["room_count"].confirmed_range == (4, 4)

    assert decisions["symbol_total"].tier == 1
    assert decisions["symbol_total"].confirmed_range == (7, 9)

    assert decisions["door_count"].tier == 3
    assert decisions["door_count"].action == "requires_review"
    assert decisions["door_count"].confirmed_range is None

    assert decisions["window_count"].tier == 3
    assert decisions["window_count"].confirmed_range is None


def test_joint_solver_uses_firewall_confirmed_range_for_tier1_targets() -> None:
    evidences_by_target = _evidence_scenario()
    decisions = _run_firewall(evidences_by_target)
    solver = _build_joint_solver(evidences_by_target, decisions)

    assert solver.variable_axis("room_count") == FIREWALL_CONFIRMED_AXIS
    assert solver.variable_axis("symbol_total") == FIREWALL_CONFIRMED_AXIS
    # 階層3の要素は、生の読み取りの軸名がそのまま残る(まだ確定していないため)。
    assert solver.variable_axis("door_count") == "image"


def test_cross_target_relation_narrows_uncalibrated_gdino_readings() -> None:
    """Grounding DINOの読み取り単体は信用できない(calibrated=False)が、
    room_count・symbol_totalという信頼できる要素との関係式を通すだけで、
    door_count/window_countの候補が2±(生の5候補から2候補)にまで絞り込める
    ことを確認する。整合性軸(段階B)とキラークエスチョン(今回)の連携効果。
    """
    evidences_by_target = _evidence_scenario()
    decisions = _run_firewall(evidences_by_target)
    solver = _build_joint_solver(evidences_by_target, decisions)

    result = solver.solve()

    assert result.is_consistent
    assert result.variables["room_count"].solved_range == (4, 4)
    assert result.variables["symbol_total"].solved_range == (8, 9)  # (7,9)から絞り込み
    assert result.variables["door_count"].solved_range == (4, 5)  # (2,6)から絞り込み
    assert result.variables["window_count"].solved_range == (4, 5)


def test_killer_question_resolves_everything_in_one_question_when_lucky() -> None:
    """door_count=5という回答が得られた場合、戸+窓=合計の関係式から
    window_countとsymbol_totalも同時に確定する(1問で全体が解決する分岐)。
    """
    evidences_by_target = _evidence_scenario()
    decisions = _run_firewall(evidences_by_target)
    solver = _build_joint_solver(evidences_by_target, decisions)

    ground_truth = {"door_count": 5, "window_count": 4, "symbol_total": 9}
    engine = KillerQuestionEngine(solver)

    first_question = engine.next_question()
    assert first_question is not None
    assert first_question.variable == "door_count"  # room_count/symbol_totalは既に確定済みなので聞かれない

    session = engine.run(lambda q: ground_truth[q.variable])

    assert session.question_count == 1
    assert session.answered[0].variable == "door_count"
    assert session.stopped_reason == "all_resolved"
    assert session.final_result.variables["window_count"].solved_range == (4, 4)
    assert session.final_result.variables["symbol_total"].solved_range == (9, 9)


def test_killer_question_asks_a_second_question_when_the_first_answer_is_ambiguous() -> None:
    """door_count=4という回答では戸+窓=合計の関係式だけでは
    window_countが(4,5)のまま決まらない(door=4なら合計8でも9でも辻褄が合う
    ため)。この場合は2問目が必要になる、という正直な分岐を確認する。
    """
    evidences_by_target = _evidence_scenario()
    decisions = _run_firewall(evidences_by_target)
    solver = _build_joint_solver(evidences_by_target, decisions)

    ground_truth = {"door_count": 4, "window_count": 4, "symbol_total": 8}
    engine = KillerQuestionEngine(solver)

    session = engine.run(lambda q: ground_truth[q.variable])

    assert session.question_count == 2
    assert session.answered[0].variable == "door_count"
    assert session.stopped_reason == "all_resolved"
    assert session.final_result.variables["window_count"].solved_range == (4, 4)
