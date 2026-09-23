"""吸収と判定された群に、キラークエスチョンを1問だけ立てる(判断の5番)。

`docs/group_total_masking_design.md` 4-1節の (c)。(a)(吸収が起きた群の
階層1を階層2へ落とす)はそのまま残し、その上に「群の中で1問」を足す。

**このファイルのテストは、実装より先に書いた**(実装前は
``KillerQuestionEngine.group_total_question`` と
``group_total_absorption_excess`` が無いので、import の段階で落ちる)。

------------------------------------------------------------------------------
何を選ぶか
------------------------------------------------------------------------------
「群の残差」は、停止した要素が群合計のせいで**自分の読みの中心窓
(中心 ± ``CENTER_TOLERANCES``)の外へ押し出された量の合計**とする
(``group_total_absorption_excess``)。これが 0 より大きいことが、
``check_group_total`` の「吸収された」と同じ意味になる。

要素ごとに「その要素に聞いて、答えが自分の読みの範囲のどれかだったら、
残差はどれだけ減るか」を、候補値それぞれについて仮に置いて測り、平均する。
答えで群が矛盾(unsat)になる場合は、残差を全部動かしたと数える
(隠れていた矛盾が表に出て、既存の安全装置が群を止めるため)。
平均がいちばん大きい要素を1つ選ぶ。
"""

from __future__ import annotations

from arbitration.consistency_solver import ConsistencySolver
from arbitration.group_total import (
    GroupTotalConstraint,
    check_group_total,
    group_total_absorption_excess,
)
from killer_question.engine import KillerQuestionEngine
from tests.test_group_total_masking import (
    GROUP,
    TRUTH,
    _add_group_total,
    _agreeing_strong_axes,
    _build_group,
    _evidence,
    _run_firewall,
)


def _stopped(target: str, count_range: tuple[int, int]) -> list:
    """強い軸1つ + 弱い軸1つ。ファイアウォールは階層3(停止)にする。"""
    return [
        _evidence(target, count_range, "drawing-A", "image"),
        _evidence(target, count_range, "自社実績DB", "history", strength="weak"),
    ]


def _absorbed_by_a_tier1_error() -> tuple[ConsistencySolver, GroupTotalConstraint]:
    """``door_1`` の強い2軸が同じ誤値1で一致(正解3)、``door_0`` が (0, 6) で停止。"""
    solver, decisions, _ = _run_firewall(
        _build_group(stopped_evidence=_stopped("door_0", (0, 6)),
                     wrong_targets={"door_1": 1})
    )
    assert decisions["door_0"].tier == 3  # type: ignore[attr-defined]
    assert decisions["door_1"].tier == 1  # type: ignore[attr-defined]
    return solver, _add_group_total(solver)


def _snapshot(solver: ConsistencySolver) -> tuple:
    names = sorted(solver.variable_names())
    return (
        tuple(names),
        tuple(solver.variable_range(n) for n in names),
        tuple(solver.variable_detection_range(n) for n in names),
        tuple(solver.requires_confirmation(n) for n in names),
        solver.constraint_names(),
    )


def test_the_absorption_excess_is_positive_exactly_when_the_group_is_absorbed() -> None:
    """残差の定義が ``check_group_total`` の「吸収された」と一致すること。"""
    solver, constraint = _absorbed_by_a_tier1_error()
    assert check_group_total(solver, constraint).is_absorbed
    # door_0 は 5 に押し出され、中心 3 ± 1 の窓 (2, 4) から 1 はみ出す。
    assert group_total_absorption_excess(solver, constraint) == 1

    clean, _d, _u = _run_firewall(
        _build_group(stopped_evidence=_stopped("door_0", (0, 6)), wrong_targets={})
    )
    clean_constraint = _add_group_total(clean)
    assert not check_group_total(clean, clean_constraint).is_absorbed
    assert group_total_absorption_excess(clean, clean_constraint) == 0


def test_the_absorption_excess_is_none_when_the_group_total_is_unsat() -> None:
    solver, _d, _u = _run_firewall(
        _build_group(stopped_evidence=_agreeing_strong_axes("door_0", 3),
                     wrong_targets={"door_1": 1, "door_2": 1})
    )
    constraint = _add_group_total(solver)
    assert group_total_absorption_excess(solver, constraint) is None


def test_an_absorbed_group_gets_exactly_one_question_on_the_element_that_moves_the_residual() -> None:
    """吸収された群には1問。聞く相手は残差を動かせる要素(ここでは停止した door_0)。

    **群合計は、階層1のどの要素が誤っているかを言えない**(door_1 の読みは
    強い2軸が一致していて、読みの範囲は (1, 1) しかない)。door_1 に聞いても
    読みの範囲の答えでは残差は動かない。door_0 に聞けば、答えが 5 なら
    吸収は説明がつき、5 以外なら群合計が矛盾して群が止まる。**どちらでも
    「隠れた吸収」の状態は1問で終わる。**
    """
    solver, constraint = _absorbed_by_a_tier1_error()
    engine = KillerQuestionEngine(solver)

    question = engine.group_total_question(constraint)

    assert question is not None
    assert question.variable == "door_0"
    assert question.selected_by == "group_residual"
    # 候補は群合計で潰した値 (5,) ではなく、door_0 自身の読みの範囲。
    # 群合計そのものが疑われているので、群合計で候補を絞らない。
    assert question.candidate_values == tuple(range(0, 7))
    assert question.score > 0


def test_a_truthful_answer_exposes_the_hidden_error_and_the_group_stops() -> None:
    """正しい答え(door_0 = 3)で、隠れていた door_1 の誤りが unsat として表に出る。"""
    solver, constraint = _absorbed_by_a_tier1_error()
    engine = KillerQuestionEngine(solver)
    question = engine.group_total_question(constraint)
    assert question is not None and TRUTH[question.variable] in question.candidate_values

    engine.answer(question.variable, TRUTH[question.variable])
    after = check_group_total(engine.solver, constraint)

    assert after.status == "unsat"
    assert after.stops_the_group
    assert set(after.targets_requiring_audit) == set(GROUP)


def test_when_the_stopped_elements_own_reading_is_off_it_is_the_one_asked() -> None:
    """設計書5節3項: 停止した要素の読みの中心そのものが誤っている場合。

    door_0 の読みは (2, 10)(中心 6)、正解は 3。他は全部正しい。群合計は
    door_0 を 3 に押し出すので吸収と判定される。**誤っているのは door_0 の読み**
    なので、聞く相手が door_0 なら誤りの要素そのものに当たっている。
    正しい答えで群は矛盾なく閉じ、吸収も消える。
    """
    solver, decisions, _ = _run_firewall(
        _build_group(stopped_evidence=_stopped("door_0", (2, 10)), wrong_targets={})
    )
    assert decisions["door_0"].tier == 3  # type: ignore[attr-defined]
    constraint = _add_group_total(solver)
    assert check_group_total(solver, constraint).absorbing_targets == ("door_0",)

    engine = KillerQuestionEngine(solver)
    question = engine.group_total_question(constraint)
    assert question is not None
    assert question.variable == "door_0"

    engine.answer("door_0", TRUTH["door_0"])
    after = check_group_total(engine.solver, constraint)
    assert after.status == "sat"
    assert not after.is_absorbed


def test_two_stopped_elements_still_get_only_one_question() -> None:
    """停止した要素が2つ吸収していても、立てる問いは群に1問。"""
    evidence = _build_group(
        stopped_evidence=_stopped("door_0", (0, 6)),
        wrong_targets={"door_1": 1, "door_2": 1, "door_3": 1},
    )
    evidence["door_5"] = _stopped("door_5", (0, 6))
    solver, _d, _u = _run_firewall(evidence)
    constraint = _add_group_total(solver)
    check = check_group_total(solver, constraint)
    assert set(check.absorbing_targets) == {"door_0", "door_5"}

    question = KillerQuestionEngine(solver).group_total_question(constraint)

    assert question is not None
    assert question.variable in {"door_0", "door_5"}


def test_no_absorption_means_no_group_question() -> None:
    """陰性対照。吸収が無い群には問いを立てない。"""
    # 誤りなし・停止あり(幅の広い読み)
    clean, _d, _u = _run_firewall(
        _build_group(stopped_evidence=_stopped("door_0", (0, 6)), wrong_targets={})
    )
    assert KillerQuestionEngine(clean).group_total_question(_add_group_total(clean)) is None

    # 誤りなし・停止なし
    tight, _d, _u = _run_firewall(
        _build_group(stopped_evidence=_agreeing_strong_axes("door_0", 3), wrong_targets={})
    )
    assert KillerQuestionEngine(tight).group_total_question(_add_group_total(tight)) is None

    # unsat の群は既存の安全装置が群ごと止める。(c) は問いを足さない。
    broken, _d, _u = _run_firewall(
        _build_group(stopped_evidence=_agreeing_strong_axes("door_0", 3),
                     wrong_targets={"door_1": 1, "door_2": 1})
    )
    broken_constraint = _add_group_total(broken)
    assert check_group_total(broken, broken_constraint).status == "unsat"
    assert KillerQuestionEngine(broken).group_total_question(broken_constraint) is None


def test_the_group_question_changes_no_tier_and_no_audit_list() -> None:
    """(c) は問いを足すだけ。(a) の降格・solver の中身・階層は何も変えない。"""
    solver, constraint = _absorbed_by_a_tier1_error()
    before_check = check_group_total(solver, constraint)
    before = _snapshot(solver)

    engine = KillerQuestionEngine(solver)
    engine_before = _snapshot(engine.solver)
    assert engine.group_total_question(constraint) is not None

    assert _snapshot(solver) == before
    assert _snapshot(engine.solver) == engine_before
    assert check_group_total(solver, constraint) == before_check
    assert check_group_total(engine.solver, constraint) == before_check
