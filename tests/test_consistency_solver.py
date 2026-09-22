"""整合性軸(arbitration/consistency_solver.py)の回帰テスト。"""

from __future__ import annotations

import pytest

from arbitration.consistency_solver import ConsistencySolver
from axes.image_axis.grounding_dino_adapter import (
    RawDetection,
    ScoredDetection,
    SymbolCountReading,
)


def _reading(
    category: str,
    count_range: tuple[int, int],
    status: str = "confident",
) -> SymbolCountReading:
    return SymbolCountReading(
        category=category,
        prompt=f"a {category} symbol in a floor plan",
        count_range=count_range,
        status=status,
    )


# --- 変数登録 -----------------------------------------------------------------


def test_add_variable_rejects_duplicate_name() -> None:
    solver = ConsistencySolver()
    solver.add_variable("x", 0, 10)
    with pytest.raises(ValueError):
        solver.add_variable("x", 0, 5)


def test_add_variable_rejects_inverted_range() -> None:
    solver = ConsistencySolver()
    with pytest.raises(ValueError):
        solver.add_variable("x", 10, 0)


def test_add_variable_from_reading_uses_count_range() -> None:
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "door_count", _reading("door", (3, 5)), axis="image_axis"
    )
    result = solver.solve()
    assert result.status == "sat"
    assert result.variables["door_count"].original_range == (3, 5)


def test_abstained_reading_adds_no_hard_constraint() -> None:
    """status == 'abstained' の読み取りは変数として登録されない(v8: 棄権)。"""
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "door_count", _reading("door", (0, 0), status="abstained"), axis="image_axis"
    )
    result = solver.solve()
    assert "door_count" not in result.variables
    assert result.abstained[0].name == "door_count"
    assert result.status == "sat"  # 制約が無いので自明に sat


# --- 通常ケース: 解が存在する ---------------------------------------------------


def test_normal_case_produces_intersected_ranges() -> None:
    """部屋数4・戸4・窓4(段階Aの合成図面と同じ構成)で、矛盾なく解が求まる。"""
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "room_count", _reading("room", (4, 4)), axis="absolute_rule_axis"
    )
    solver.add_variable_from_reading(
        "door_count", _reading("door", (3, 5)), axis="image_axis"
    )
    solver.add_variable_from_reading(
        "window_count", _reading("window", (3, 5)), axis="image_axis"
    )
    # IfcOpenShell の space_without_door / space_without_window 相当のルール:
    # 各部屋に戸・窓が最低1つずつ必要 -> 合計は部屋数以上。
    solver.add_relation("door_ge_room", "door_count", ">=", "room_count")
    solver.add_relation("window_ge_room", "window_count", ">=", "room_count")

    result = solver.solve()

    assert result.status == "sat"
    # 戸・窓の読み取りレンジ (3,5) は、door_count>=4 という制約で下限が絞り込まれる。
    assert result.variables["door_count"].solved_range == (4, 5)
    assert result.variables["window_count"].solved_range == (4, 5)
    assert result.variables["room_count"].solved_range == (4, 4)


def test_relation_with_offset_expression() -> None:
    """「開き戸の数 = 部屋数 + 1」のような、指示書の例そのものの関係式。"""
    solver = ConsistencySolver()
    solver.add_variable("room_count", 4, 4, axis="absolute_rule_axis")
    solver.add_variable("door_count", 0, 10, axis="image_axis", strength="strong")
    solver.add_relation(
        "door_eq_room_plus_1", "door_count", "==", lambda v: v["room_count"] + 1
    )

    result = solver.solve()

    assert result.status == "sat"
    assert result.variables["door_count"].solved_range == (5, 5)


# --- 矛盾ケース: 解が存在しない -------------------------------------------------


def test_contradiction_is_detected_as_unsat_with_conflicting_constraints() -> None:
    """読み取った窓の数が、部屋数から導かれる制約と矛盾するケース。"""
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "room_count", _reading("room", (4, 4)), axis="absolute_rule_axis"
    )
    # 劣化により窓を1つ読み落とし、confident に (3,3) と断定してしまった状況。
    solver.add_variable_from_reading(
        "window_count", _reading("window", (3, 3)), axis="image_axis"
    )
    solver.add_relation("window_ge_room", "window_count", ">=", "room_count")

    result = solver.solve()

    assert result.status == "unsat"
    # room_count・window_count がどちらも単一値に固定されているため、
    # 関係式に加えて両方のレンジ制約も矛盾の必要条件として core に含まれる
    # (どちらか一方でも欠けば、変数がその分自由に動けて sat になり得るため)。
    assert set(result.conflicting_constraints) == {
        "window_ge_room",
        "range::room_count",
        "range::window_count",
    }


def test_conflicting_range_constraints_are_named_in_core() -> None:
    solver = ConsistencySolver()
    solver.add_variable("x", 0, 3, axis="a")
    solver.add_variable("y", 5, 10, axis="b")
    solver.add_relation("x_eq_y", "x", "==", "y")

    result = solver.solve()

    assert result.status == "unsat"
    assert set(result.conflicting_constraints) == {"range::x", "range::y", "x_eq_y"}


# --- 強い軸 / 弱い軸の区別 ------------------------------------------------------


def test_weak_axis_reading_never_becomes_a_hard_variable() -> None:
    """弱い軸として登録した変数は、そもそも add_variable_from_reading の対象外であり、
    add_advisory_reading は変数を作らないことを確認する。
    """
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "room_count", _reading("room", (4, 4)), axis="absolute_rule_axis"
    )
    # VTracer 由来の壁長からの推定部屋数(弱い軸)。強い軸の解と矛盾する値でも
    # 解には一切影響しないはず。
    solver.add_advisory_reading(
        "room_count", _reading("room_from_wall_geometry", (10, 10)), axis="vtracer"
    )

    result = solver.solve()

    assert result.status == "sat"
    assert result.variables["room_count"].solved_range == (4, 4)
    assert len(result.advisories) == 1
    note = result.advisories[0]
    assert note.axis == "vtracer"
    assert note.agrees is False  # (10,10) は (4,4) と重ならない
    assert "重ならない" in note.message


def test_weak_axis_reading_that_agrees_is_reported_as_agreeing() -> None:
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "door_count", _reading("door", (4, 4)), axis="absolute_rule_axis"
    )
    solver.add_advisory_reading(
        "door_count", _reading("door_low_conf", (3, 5), status="low_confidence"), axis="image_axis"
    )

    result = solver.solve()

    assert result.status == "sat"
    note = result.advisories[0]
    assert note.agrees is True


def test_advisory_abstained_reading_is_recorded_but_not_compared() -> None:
    solver = ConsistencySolver()
    solver.add_variable_from_reading(
        "door_count", _reading("door", (4, 4)), axis="absolute_rule_axis"
    )
    solver.add_advisory_reading(
        "door_count", _reading("door", (0, 0), status="abstained"), axis="image_axis"
    )

    result = solver.solve()

    assert result.status == "sat"
    assert result.advisories == ()
    assert result.abstained[0].axis == "image_axis"


def test_advisory_with_no_matching_strong_variable_is_reported_as_incomparable() -> None:
    solver = ConsistencySolver()
    solver.add_advisory_reading(
        "unregistered_variable", _reading("x", (1, 2)), axis="image_axis"
    )
    result = solver.solve()

    assert result.status == "sat"
    note = result.advisories[0]
    assert note.solved_range is None
    assert note.agrees is None


# --- describe() ------------------------------------------------------------


def test_describe_does_not_raise_for_sat_and_unsat() -> None:
    sat_solver = ConsistencySolver()
    sat_solver.add_variable("x", 0, 1, axis="a")
    assert "status=sat" in sat_solver.solve().describe()

    unsat_solver = ConsistencySolver()
    unsat_solver.add_variable("x", 0, 1, axis="a")
    unsat_solver.add_relation("impossible", "x", ">", 100)
    assert "status=unsat" in unsat_solver.solve().describe()
