"""killer_question/dependency_graph.py の単体テスト。

依存関係の"事実"は consistency_solver 側にあり、ここでは「それを正しく
読み取ってグラフ化できているか」だけを検証する(制約の意味そのものは
tests/test_consistency_solver.py の責務)。
"""

from __future__ import annotations

from arbitration.consistency_solver import ConsistencySolver
from killer_question.dependency_graph import build_dependency_graph


def test_two_variable_relation_creates_a_bidirectional_edge() -> None:
    solver = ConsistencySolver()
    solver.add_variable("a", 0, 5, axis="x")
    solver.add_variable("b", 0, 5, axis="x")
    solver.add_relation("a_eq_b", "a", "==", "b")

    graph = build_dependency_graph(solver)

    assert graph.nodes == frozenset({"a", "b"})
    assert graph.neighbors("a") == frozenset({"b"})
    assert graph.neighbors("b") == frozenset({"a"})


def test_lambda_based_relation_is_introspected_correctly() -> None:
    """add_constraint に生の関数を渡した場合も、__getitem__ アクセスの記録で
    参照変数を検出できることを確認する(add_relation 経由と同じ扱い)。
    """
    solver = ConsistencySolver()
    solver.add_variable("room_count", 3, 5, axis="rules")
    solver.add_variable("door_count", 0, 20, axis="image")
    solver.add_constraint(
        "door_ge_room_plus_1",
        lambda v: v["door_count"] >= v["room_count"] + 1,
    )

    graph = build_dependency_graph(solver)

    assert graph.neighbors("room_count") == frozenset({"door_count"})
    assert graph.neighbors("door_count") == frozenset({"room_count"})


def test_single_variable_constraint_creates_no_edge() -> None:
    """単項の制約(1変数だけを参照する式)は、依存関係の辺にはならない。"""
    solver = ConsistencySolver()
    solver.add_variable("a", 0, 10, axis="x")
    solver.add_variable("b", 0, 10, axis="x")
    solver.add_constraint("a_is_even_ish", lambda v: v["a"] >= 0)  # bを参照しない

    graph = build_dependency_graph(solver)

    assert graph.neighbors("a") == frozenset()
    assert graph.neighbors("b") == frozenset()


def test_three_variable_relation_connects_all_pairs() -> None:
    solver = ConsistencySolver()
    for name in ("total", "a", "b"):
        solver.add_variable(name, 0, 20, axis="x")
    solver.add_constraint("total_eq_a_plus_b", lambda v: v["total"] == v["a"] + v["b"])

    graph = build_dependency_graph(solver)

    assert graph.neighbors("total") == frozenset({"a", "b"})
    assert graph.neighbors("a") == frozenset({"total", "b"})
    assert graph.neighbors("b") == frozenset({"total", "a"})


def test_connected_component_excludes_the_node_itself() -> None:
    solver = ConsistencySolver()
    solver.add_variable("a", 0, 5, axis="x")
    solver.add_variable("b", 0, 5, axis="x")
    solver.add_variable("c", 0, 5, axis="x")
    solver.add_relation("a_eq_b", "a", "==", "b")
    solver.add_relation("b_eq_c", "b", "==", "c")

    graph = build_dependency_graph(solver)

    assert graph.connected_component("a") == frozenset({"b", "c"})
    assert "a" not in graph.connected_component("a")


def test_disconnected_variables_have_empty_connected_component() -> None:
    solver = ConsistencySolver()
    solver.add_variable("isolated", 0, 5, axis="x")
    solver.add_variable("a", 0, 5, axis="x")
    solver.add_variable("b", 0, 5, axis="x")
    solver.add_relation("a_eq_b", "a", "==", "b")

    graph = build_dependency_graph(solver)

    assert graph.connected_component("isolated") == frozenset()


def test_components_splits_the_graph_into_clusters() -> None:
    solver = ConsistencySolver()
    solver.add_variable("a1", 0, 5, axis="x")
    solver.add_variable("a2", 0, 5, axis="x")
    solver.add_relation("a1_eq_a2", "a1", "==", "a2")
    solver.add_variable("b1", 0, 5, axis="x")

    graph = build_dependency_graph(solver)
    components = graph.components()

    assert frozenset({"a1", "a2"}) in components
    assert frozenset({"b1"}) in components
    assert len(components) == 2


def test_constraints_by_edge_records_which_constraint_created_the_edge() -> None:
    solver = ConsistencySolver()
    solver.add_variable("a", 0, 5, axis="x")
    solver.add_variable("b", 0, 5, axis="x")
    solver.add_relation("a_eq_b", "a", "==", "b")

    graph = build_dependency_graph(solver)

    assert graph.constraints_by_edge[frozenset({"a", "b"})] == ("a_eq_b",)
