"""キラークエスチョンエンジン(killer_question/engine.py)の回帰テスト。

段階1(トライアル7〜9の再現)
------------------------------
トライアル7〜9で使われた4種類の依存グラフ構造(スター型、独立2上流型、
多段型、合流型)そのものは、このリポジトリには残っていない(段階Aの
合成図面が残っていなかったのと同じ事情)。そのため、同じ構造の特徴
(理論下限の質問数、データ源考慮による質問順の変化)を再現できる
同等の固定シナリオをここで新たに構成し、検証している。数値そのものは
このリポジトリのシナリオに基づくもので、トライアル7〜9の元の数値とは
比較できない。
"""

from __future__ import annotations

from arbitration.consistency_solver import ConsistencySolver
from killer_question.dependency_graph import build_dependency_graph
from killer_question.engine import KillerQuestionEngine, Question


def _oracle(ground_truth: dict[str, int]):
    def answer_fn(question: Question) -> int:
        return ground_truth[question.variable]

    return answer_fn


# ---------------------------------------------------------------------------
# 段階1-1: スター型(1つの上流が複数の下流を等式で決定する)
# ---------------------------------------------------------------------------


def _build_star() -> ConsistencySolver:
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="text_axis")
    solver.add_variable("leaf_1", 0, 10, axis="image_axis")
    solver.add_variable("leaf_2", 0, 10, axis="image_axis")
    solver.add_variable("leaf_3", 0, 10, axis="image_axis")
    for leaf in ("leaf_1", "leaf_2", "leaf_3"):
        solver.add_relation(f"{leaf}_eq_room", leaf, "==", "room_count")
    return solver


def test_star_all_nodes_tie_at_the_same_score() -> None:
    """等式で結ばれたスター型では、どのノードを固定しても全体が決まるため、
    ハブと葉のスコアが理論上一致する(全ノードが1つの等価類になる)。
    """
    solver = _build_star()
    result = solver.solve()
    graph = build_dependency_graph(solver)
    engine = KillerQuestionEngine(solver)

    scores = {
        name: engine.score_candidate(result, graph, name).score
        for name in ("room_count", "leaf_1", "leaf_2", "leaf_3")
    }
    assert len(set(scores.values())) == 1
    assert next(iter(scores.values())) == 6.0  # 下流3件 × 削減幅2、全候補で一定


def test_star_resolves_in_one_question_matching_theoretical_minimum() -> None:
    """4変数が1つの等価類なので、理論下限の質問数は1。"""
    solver = _build_star()
    ground_truth = {"room_count": 4, "leaf_1": 4, "leaf_2": 4, "leaf_3": 4}
    engine = KillerQuestionEngine(solver)

    session = engine.run(_oracle(ground_truth))

    assert session.question_count == 1
    assert session.stopped_reason == "all_resolved"
    assert session.remaining_unresolved == ()


def test_star_data_source_error_rate_changes_the_question_order() -> None:
    """トライアル9のルール: スコアが同点なら誤り率の低いデータ源を優先する。

    誤り率を考慮しない場合は変数名の昇順(leaf_1)が選ばれるが、
    text_axis(誤り率0.05)をimage_axis(誤り率0.30)より優先させると、
    room_countが選ばれるようになる(質問順が変わる)。
    """
    solver = _build_star()

    without_rates = KillerQuestionEngine(solver)
    question_without = without_rates.next_question()
    assert question_without is not None
    assert question_without.variable == "leaf_1"  # 名前順のタイブレークのみ
    assert question_without.tie_broken_by_axis is True

    with_rates = KillerQuestionEngine(
        solver, axis_error_rates={"text_axis": 0.05, "image_axis": 0.30}
    )
    question_with = with_rates.next_question()
    assert question_with is not None
    assert question_with.variable == "room_count"  # 誤り率の低い軸が優先される
    assert question_with.tie_broken_by_axis is True

    assert question_without.variable != question_with.variable


# ---------------------------------------------------------------------------
# 段階1-2: 独立2上流型(互いに無関係な2つのクラスタ)
# ---------------------------------------------------------------------------


def _build_two_independent_clusters() -> ConsistencySolver:
    solver = ConsistencySolver()
    # クラスタA: 3変数
    solver.add_variable("hub_a", 3, 5, axis="a")
    solver.add_variable("leaf_a1", 0, 10, axis="a")
    solver.add_variable("leaf_a2", 0, 10, axis="a")
    solver.add_relation("leaf_a1_eq_hub_a", "leaf_a1", "==", "hub_a")
    solver.add_relation("leaf_a2_eq_hub_a", "leaf_a2", "==", "hub_a")
    # クラスタB: 2変数。Aとは一切関係を持たない。
    solver.add_variable("hub_b", 10, 12, axis="b")
    solver.add_variable("leaf_b1", 0, 20, axis="b")
    solver.add_relation("leaf_b1_eq_hub_b", "leaf_b1", "==", "hub_b")
    return solver


def test_two_independent_clusters_do_not_influence_each_other() -> None:
    solver = _build_two_independent_clusters()
    result = solver.solve()
    graph = build_dependency_graph(solver)

    assert graph.connected_component("hub_a") == frozenset({"leaf_a1", "leaf_a2"})
    assert graph.connected_component("hub_b") == frozenset({"leaf_b1"})
    assert len(graph.components()) == 2

    engine = KillerQuestionEngine(solver)
    score_hub_a = engine.score_candidate(result, graph, "hub_a").score
    score_hub_b = engine.score_candidate(result, graph, "hub_b").score
    # クラスタAは下流2件、クラスタBは下流1件のため、Aの方が削減効果が大きい。
    assert score_hub_a == 4.0
    assert score_hub_b == 2.0
    assert score_hub_a > score_hub_b


def test_two_independent_clusters_require_two_questions_one_per_cluster() -> None:
    solver = _build_two_independent_clusters()
    ground_truth = {
        "hub_a": 4, "leaf_a1": 4, "leaf_a2": 4,
        "hub_b": 11, "leaf_b1": 11,
    }
    engine = KillerQuestionEngine(solver)

    first_question = engine.next_question()
    assert first_question is not None
    assert first_question.variable in {"hub_a", "leaf_a1", "leaf_a2"}  # スコアが高いクラスタA

    session = engine.run(_oracle(ground_truth))

    assert session.question_count == 2  # 理論下限: クラスタごとに最低1問
    assert session.stopped_reason == "all_resolved"
    asked_clusters = {
        ("A" if q.variable.endswith("_a") or q.variable == "hub_a" else "B")
        for q in session.answered
    }
    assert asked_clusters == {"A", "B"}  # 両クラスタから1問ずつ


# ---------------------------------------------------------------------------
# 段階1-3: 多段型(A→B→Cの鎖状の依存関係)
# ---------------------------------------------------------------------------


def _build_chain() -> ConsistencySolver:
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="rules")
    solver.add_variable("door_count", 0, 20, axis="image")
    solver.add_variable("window_count", 0, 20, axis="image")
    solver.add_relation("door_eq_room_plus_1", "door_count", "==", lambda v: v["room_count"] + 1)
    solver.add_relation("window_eq_door_plus_1", "window_count", "==", lambda v: v["door_count"] + 1)
    return solver


def test_chain_dependency_reaches_across_more_than_one_hop() -> None:
    """A-B-Cという鎖状の構造でも、room_countの連結成分にwindow_countまで含まれる
    (直接の制約はdoor_countとの間にしかないが、Z3の大域求解による波及効果を
    正しく捉えられているかの確認)。
    """
    solver = _build_chain()
    graph = build_dependency_graph(solver)
    assert graph.connected_component("room_count") == frozenset({"door_count", "window_count"})


def test_chain_resolves_in_one_question_despite_being_multi_hop() -> None:
    """正直な発見: Z3は逐次伝播ではなく大域求解のため、鎖の"何段目"を聞いても
    1問で鎖全体が確定する(docs/killer_question_report.md 5節で議論)。
    """
    solver = _build_chain()
    ground_truth = {"room_count": 4, "door_count": 5, "window_count": 6}
    engine = KillerQuestionEngine(solver)

    session = engine.run(_oracle(ground_truth))

    assert session.question_count == 1
    assert session.stopped_reason == "all_resolved"


# ---------------------------------------------------------------------------
# 段階1-4: 合流型(複数の上流が既知の合計に収束する)
# ---------------------------------------------------------------------------


def _build_confluence() -> ConsistencySolver:
    solver = ConsistencySolver()
    solver.add_variable("door_count", 2, 6, axis="image")
    solver.add_variable("window_count", 2, 6, axis="image")
    solver.add_variable("symbol_total", 8, 8, axis="absolute_rule_axis")  # 既知の合計
    solver.add_relation(
        "total_is_door_plus_window",
        "symbol_total",
        "==",
        lambda v: v["door_count"] + v["window_count"],
    )
    return solver


def test_confluence_learning_one_upstream_determines_the_other() -> None:
    solver = _build_confluence()
    ground_truth = {"door_count": 3, "window_count": 5}
    engine = KillerQuestionEngine(solver)

    first_question = engine.next_question()
    assert first_question is not None
    assert first_question.variable in {"door_count", "window_count"}
    # 既に確定済み(symbol_total)は質問候補に上がらない。
    assert first_question.variable != "symbol_total"

    session = engine.run(_oracle(ground_truth))

    assert session.question_count == 1
    assert session.stopped_reason == "all_resolved"


# ---------------------------------------------------------------------------
# 補足: 不等式による非対称なスコア(タイブレークに頼らない優先順位付けの確認)
# ---------------------------------------------------------------------------


def test_asymmetric_relation_makes_hub_score_higher_than_any_single_leaf() -> None:
    """トライアル7〜9の4構造とは別に追加した検証。等式だけだと全ノードが
    等価類になりタイブレークに頼ってしまうため、不等式で結んだハブ型を作り、
    タイブレークなしでハブが選ばれることを確認する。
    """
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="rules")
    for i in (1, 2, 3):
        solver.add_variable(f"count_{i}", 0, 10, axis="image")
        solver.add_relation(f"count_{i}_ge_room", f"count_{i}", ">=", "room_count")

    result = solver.solve()
    graph = build_dependency_graph(solver)
    engine = KillerQuestionEngine(solver)

    hub_score = engine.score_candidate(result, graph, "room_count").score
    leaf_scores = [
        engine.score_candidate(result, graph, f"count_{i}").score for i in (1, 2, 3)
    ]

    assert hub_score == 3.0
    assert all(leaf_score < hub_score for leaf_score in leaf_scores)

    question = engine.next_question()
    assert question is not None
    assert question.variable == "room_count"
    assert question.tie_broken_by_axis is False  # タイブレークなしで単独最高スコア


# ---------------------------------------------------------------------------
# 終了条件・境界ケース
# ---------------------------------------------------------------------------


def test_already_fully_resolved_solver_needs_no_questions() -> None:
    solver = ConsistencySolver()
    solver.add_variable("x", 4, 4, axis="a")
    engine = KillerQuestionEngine(solver)

    session = engine.run(lambda q: (_ for _ in ()).throw(AssertionError("聞かれるはずがない")))

    assert session.question_count == 0
    assert session.stopped_reason == "all_resolved"


def test_unsat_solver_is_reported_without_asking_questions() -> None:
    solver = ConsistencySolver()
    solver.add_variable("x", 0, 3, axis="a")
    solver.add_variable("y", 5, 10, axis="a")
    solver.add_relation("x_eq_y", "x", "==", "y")
    engine = KillerQuestionEngine(solver)

    session = engine.run(lambda q: (_ for _ in ()).throw(AssertionError("聞かれるはずがない")))

    assert session.question_count == 0
    assert session.stopped_reason == "unsat"


def test_isolated_variable_is_never_asked_and_remains_unresolved() -> None:
    """依存関係を持たない要素は、killer_questionの対象外(v8 3-3節の
    階層3へそのまま回す運用を想定)。スコアが常に0のため、無限に質問され続ける
    ことはなく、"no_further_reduction"として素直に打ち切られる。
    """
    solver = ConsistencySolver()
    solver.add_variable("isolated", 0, 5, axis="a")
    engine = KillerQuestionEngine(solver)

    session = engine.run(lambda q: (_ for _ in ()).throw(AssertionError("聞かれるはずがない")))

    assert session.question_count == 0
    assert session.stopped_reason == "no_further_reduction"
    assert session.remaining_unresolved == ("isolated",)


def test_run_rejects_an_answer_outside_the_candidate_range() -> None:
    solver = _build_star()
    engine = KillerQuestionEngine(solver)

    import pytest

    with pytest.raises(ValueError):
        engine.run(lambda q: 999)
