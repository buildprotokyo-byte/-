"""精度モード(精密/標準/概算)・インパクト加重スコア・累積確信度カバレッジの検証。

既存のkiller_questionエンジン(段階7)には手を加えず、単価(unit_prices)と
モード(mode)を渡さない限り既存動作(reduction_score単体でのランキング、
標準モードの停止条件)と完全に一致することを、既存テスト215件がそのまま
パスすることで確認している(このファイルでは新規機能だけを検証する)。
"""

from __future__ import annotations

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine
from killer_question.firewall_bridge import (
    FIREWALL_PROVISIONAL_AXIS,
    add_target_to_joint_solver,
)
from killer_question.precision_mode import PrecisionMode


# ---------------------------------------------------------------------------
# インパクト加重スコア(score = reduction_score × impact)
# ---------------------------------------------------------------------------


def _build_two_clusters() -> ConsistencySolver:
    solver = ConsistencySolver()
    # クラスタA: reduction_scoreは高い(4.0)が、単価は付けない。
    solver.add_variable("hub_a", 3, 5, axis="a")
    solver.add_variable("leaf_a1", 0, 10, axis="a")
    solver.add_variable("leaf_a2", 0, 10, axis="a")
    solver.add_relation("leaf_a1_eq_hub_a", "leaf_a1", "==", "hub_a")
    solver.add_relation("leaf_a2_eq_hub_a", "leaf_a2", "==", "hub_a")
    # クラスタB: reduction_scoreは低い(1.0)。
    solver.add_variable("hub_b", 100, 101, axis="b")
    solver.add_variable("leaf_b1", 0, 200, axis="b")
    solver.add_relation("leaf_b1_eq_hub_b", "leaf_b1", "==", "hub_b")
    return solver


def test_missing_unit_price_defaults_to_no_weighting() -> None:
    """単価が未登録の要素は impact=1.0 となり、score == reduction_score になる。"""
    solver = _build_two_clusters()
    result = solver.solve()
    from killer_question.dependency_graph import build_dependency_graph

    graph = build_dependency_graph(solver)
    engine = KillerQuestionEngine(solver)

    candidate = engine.score_candidate(result, graph, "hub_a")
    assert candidate.impact == 1.0
    assert candidate.score == candidate.reduction_score == 4.0


def test_impact_weighting_can_flip_the_selection_order() -> None:
    """reduction_scoreが低くても、単価が非常に高ければ選ばれる要素が変わる。"""
    solver = _build_two_clusters()

    without_price = KillerQuestionEngine(solver)
    question_without = without_price.next_question()
    assert question_without is not None
    assert question_without.variable == "hub_a"  # reduction_score 4.0 > 1.0

    with_price = KillerQuestionEngine(solver, unit_prices={"hub_b": 10_000})
    question_with = with_price.next_question()
    assert question_with is not None
    assert question_with.variable == "hub_b"  # 1.0 * (10_000 * 100.5) >> 4.0 * 1.0

    assert question_without.variable != question_with.variable


# ---------------------------------------------------------------------------
# 精密モード: 依存関係が無くても全要素が解決するまで質問を続ける
# ---------------------------------------------------------------------------


def test_precise_mode_asks_about_isolated_variables_unlike_standard() -> None:
    def build() -> ConsistencySolver:
        solver = ConsistencySolver()
        solver.add_variable("isolated", 0, 5, axis="a")
        return solver

    standard_engine = KillerQuestionEngine(build(), mode=PrecisionMode.STANDARD)
    standard_session = standard_engine.run(
        lambda q: (_ for _ in ()).throw(AssertionError("標準モードでは聞かれないはず"))
    )
    assert standard_session.question_count == 0
    assert standard_session.stopped_reason == "no_further_reduction"

    precise_engine = KillerQuestionEngine(build(), mode=PrecisionMode.PRECISE)
    precise_session = precise_engine.run(lambda q: 3)
    assert precise_session.question_count == 1
    assert precise_session.stopped_reason == "all_resolved"
    assert precise_session.answered[0].variable == "isolated"


def test_precise_mode_treats_tier2_firewall_decisions_as_needing_confirmation() -> None:
    """階層2(仮採用)は、標準モードでは確定範囲を使うが、精密モードでは
    標本監査だけで済ませず、生の読み取りレンジ(まだ確定していない)を使う。
    """
    evidences = [
        AxisEvidence(target="x", count_range=(5, 5), source_id="strong-src",
                     axis_id="rules", method_id="strong_method", calibrated=True),
        AxisEvidence(target="x", count_range=(4, 6), source_id="weak-src-1",
                     axis_id="history", method_id="prior1", calibrated=True, strength="weak"),
        AxisEvidence(target="x", count_range=(4, 6), source_id="weak-src-2",
                     axis_id="statistical", method_id="prior2", calibrated=True, strength="weak"),
    ]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.tier == 2
    assert decision.action == "provisional_audit"
    assert decision.confirmed_range == (5, 5)

    standard_solver = ConsistencySolver()
    add_target_to_joint_solver(standard_solver, "x", decision, evidences, mode=PrecisionMode.STANDARD)
    assert standard_solver.solve().variables["x"].solved_range == (5, 5)  # 既に確定済み
    assert standard_solver.variable_axis("x") == FIREWALL_PROVISIONAL_AXIS

    precise_solver = ConsistencySolver()
    add_target_to_joint_solver(precise_solver, "x", decision, evidences, mode=PrecisionMode.PRECISE)
    assert precise_solver.solve().variables["x"].solved_range == (4, 6)  # まだ未確定
    assert precise_solver.variable_axis("x") == "rules"


# ---------------------------------------------------------------------------
# 概算モード: 金額インパクト優先 + 累積確信度カバレッジでの打ち切り
# ---------------------------------------------------------------------------


def _build_priced_items() -> ConsistencySolver:
    solver = ConsistencySolver()
    solver.add_variable("item_A", 9, 11, axis="a")
    solver.add_variable("item_B", 9, 11, axis="b")
    solver.add_variable("item_C", 9, 11, axis="c")
    solver.add_variable("item_D", 9, 11, axis="d")
    return solver


_ITEM_PRICES = {"item_A": 100, "item_B": 50, "item_C": 30, "item_D": 20}
_ITEM_GROUND_TRUTH = {"item_A": 10, "item_B": 10, "item_C": 10, "item_D": 10}


def test_rough_mode_asks_highest_impact_items_first() -> None:
    engine = KillerQuestionEngine(
        _build_priced_items(), unit_prices=_ITEM_PRICES,
        mode=PrecisionMode.ROUGH, target_coverage=0.99,
    )
    session = engine.run(lambda q: _ITEM_GROUND_TRUTH[q.variable])
    asked_order = [a.variable for a in session.answered]
    # 単価の高い順(A=100, B=50, C=30, D=20)に聞かれるはず。
    assert asked_order == ["item_A", "item_B", "item_C", "item_D"]


def test_rough_mode_stops_once_target_coverage_is_reached() -> None:
    engine = KillerQuestionEngine(
        _build_priced_items(), unit_prices=_ITEM_PRICES,
        mode=PrecisionMode.ROUGH, target_coverage=0.70,
    )
    session = engine.run(lambda q: _ITEM_GROUND_TRUTH[q.variable])

    assert session.question_count == 2  # item_A, item_B (累積75%)
    assert session.stopped_reason == "coverage_reached"
    assert session.final_coverage is not None and session.final_coverage >= 0.70
    assert session.remaining_unresolved == ("item_C", "item_D")


def test_target_coverage_changes_the_question_count() -> None:
    """目標カバレッジを70%・85%・95%と変えた場合の質問数の変化を確認する
    (指示書の検証項目そのもの)。
    """
    results = {}
    for target in (0.70, 0.85, 0.95):
        engine = KillerQuestionEngine(
            _build_priced_items(), unit_prices=_ITEM_PRICES,
            mode=PrecisionMode.ROUGH, target_coverage=target,
        )
        session = engine.run(lambda q: _ITEM_GROUND_TRUTH[q.variable])
        results[target] = session.question_count

    assert results[0.70] == 2
    assert results[0.85] == 3
    assert results[0.95] == 4  # 100%まで到達しないと95%を満たせない
    # 目標が厳しくなるほど、質問数は単調に増える(減ることはない)。
    assert results[0.70] <= results[0.85] <= results[0.95]


def test_rough_mode_falls_back_to_standard_stopping_without_any_price() -> None:
    """価格情報が一切無ければ、概算モードのカバレッジ判定は無効化され、
    標準モードと同じ基準にフォールバックする。
    """
    solver = ConsistencySolver()
    solver.add_variable("isolated", 0, 5, axis="a")
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.ROUGH, target_coverage=0.85)

    session = engine.run(
        lambda q: (_ for _ in ()).throw(AssertionError("価格が無いのに聞かれるはず"))
    )

    assert session.question_count == 0
    assert session.stopped_reason == "no_further_reduction"
    assert session.final_coverage is None


# ---------------------------------------------------------------------------
# 3モード比較(指示書の検証項目そのもの)
# ---------------------------------------------------------------------------


def _build_mixed_scenario() -> ConsistencySolver:
    solver = ConsistencySolver()
    # 依存関係を持つクラスタ(単価なし)
    solver.add_variable("hub_a", 3, 5, axis="a")
    solver.add_variable("leaf_a1", 0, 10, axis="a")
    solver.add_variable("leaf_a2", 0, 10, axis="a")
    solver.add_relation("leaf_a1_eq_hub_a", "leaf_a1", "==", "hub_a")
    solver.add_relation("leaf_a2_eq_hub_a", "leaf_a2", "==", "hub_a")
    # 依存関係を持たない、高額・低額の要素
    solver.add_variable("isolated_expensive", 8, 12, axis="x")
    solver.add_variable("isolated_cheap", 1, 3, axis="y")
    return solver


_MIXED_GROUND_TRUTH = {
    "hub_a": 4, "leaf_a1": 4, "leaf_a2": 4,
    "isolated_expensive": 10, "isolated_cheap": 2,
}
_MIXED_PRICES = {"isolated_expensive": 1_000_000, "isolated_cheap": 1}


def test_three_modes_on_the_same_scenario_behave_differently() -> None:
    """標準モードは金額の大小を考慮しないため、高額要素を未解決のまま
    打ち切ってしまう。概算モードはその高額要素だけ拾って打ち切り、
    精密モードは安い要素まで含めて全て解決する。
    """
    standard = KillerQuestionEngine(
        _build_mixed_scenario(), unit_prices=_MIXED_PRICES, mode=PrecisionMode.STANDARD
    )
    standard_session = standard.run(lambda q: _MIXED_GROUND_TRUTH[q.variable])

    precise = KillerQuestionEngine(
        _build_mixed_scenario(), unit_prices=_MIXED_PRICES, mode=PrecisionMode.PRECISE
    )
    precise_session = precise.run(lambda q: _MIXED_GROUND_TRUTH[q.variable])

    rough = KillerQuestionEngine(
        _build_mixed_scenario(), unit_prices=_MIXED_PRICES,
        mode=PrecisionMode.ROUGH, target_coverage=0.85,
    )
    rough_session = rough.run(lambda q: _MIXED_GROUND_TRUTH[q.variable])

    # 標準モード: 依存関係のあるクラスタだけ解決し、高額要素は未解決のまま
    # 打ち切ってしまう(金額を考慮しないことの弱点がそのまま出る)。
    assert standard_session.question_count == 1
    assert standard_session.stopped_reason == "no_further_reduction"
    assert "isolated_expensive" in standard_session.remaining_unresolved

    # 概算モード: 高額要素(isolated_expensive)は拾うが、安い要素
    # (isolated_cheap)は目標カバレッジ到達後は聞かない。
    assert rough_session.question_count == 2
    assert "isolated_expensive" in [a.variable for a in rough_session.answered]
    assert rough_session.remaining_unresolved == ("isolated_cheap",)
    assert rough_session.final_coverage is not None and rough_session.final_coverage >= 0.85

    # 精密モード: 安い要素も含めて全て解決する。
    assert precise_session.question_count == 3
    assert precise_session.stopped_reason == "all_resolved"
    assert precise_session.remaining_unresolved == ()

    # 質問数は 標準 <= 概算 <= 精密 の順になる(このシナリオでは)。
    assert standard_session.question_count <= rough_session.question_count <= precise_session.question_count
