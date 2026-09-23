"""問いの質を、選び方と答えの扱いに効かせる。

2026-09-23、おーちゃんが札で 3 つとも選んだ。

1. **選び方を二段階に**(答えを検算できる問いを先に出す)
2. **検算できない問いの答えは仮説として扱い、自動確定に上げない**
3. **仕組みの予想の外にある答えを突き返さず、食い違いとして残す**

測った裏づけ:

- 58 周目 `docs/d_question_quality_report.md`: **正解 +1 の嘘は 5 問とも素通り**
- 60 周目 `docs/d_correct_answer_refused_v2_report.md`:
  **読みがずれていると、正しい答えのほうが 7 組中 6 組で突き返される**
"""

from __future__ import annotations

from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine


def _two_choices() -> ConsistencySolver:
    """検算できる問いと、できない問いを 1 つずつ持つ案件。

    `checked` は**別のデータ源の読みが 2 つある**ので A。
    `alone` は**読みが 1 つも無い**ので C。
    効きは `alone` のほうが大きくなるように、下流を 2 つぶら下げる。
    """
    solver = ConsistencySolver()
    solver.add_variable("checked", 3, 5, axis="image", independent_sources=2)
    solver.add_variable("checked_leaf", 0, 10, axis="image")
    solver.add_relation("checked_leaf_eq", "checked_leaf", "==", "checked")
    solver.add_variable("alone", 0, 10, axis="text", independent_sources=0)
    for leaf in ("leaf_1", "leaf_2"):
        solver.add_variable(leaf, 0, 10, axis="image")
        solver.add_relation(f"{leaf}_eq_alone", leaf, "==", "alone")
    return solver


def _with_a_dependent() -> ConsistencySolver:
    """**現に問いが出る案件。**下流が無い要素は質問の対象にならないので、
    「突き返さない」を測るにはぶら下がりが要る(この土台が無いと、
    1 問も出ないまま全部の主張が素通りする)。
    """
    solver = ConsistencySolver()
    solver.add_variable("count", 3, 5, axis="image", independent_sources=1)
    solver.add_variable("dependent", 0, 10, axis="image")
    solver.add_relation("dependent_eq_count", "dependent", "==", "count")
    return solver


def test_a_question_that_can_be_checked_comes_first() -> None:
    """**効きが小さくても、検算できる問いを先に出す。**"""
    engine = KillerQuestionEngine(_two_choices())
    first = engine.next_question()

    assert first is not None
    assert first.variable == "checked"
    assert first.grade == "A"


def test_the_answer_to_an_unchecked_question_is_marked_as_a_hypothesis() -> None:
    """**検算できない問いの答えは、仮説として記録される。**"""
    engine = KillerQuestionEngine(_two_choices())
    session = engine.run(lambda q: q.candidate_values[0])

    by_variable = {a.variable: a for a in session.answered}
    assert by_variable["checked"].basis == "read"
    assert by_variable["alone"].basis == "assumed"
    assert session.assumed_variables == ("alone",)


def test_an_answer_outside_the_candidates_is_kept_as_a_disagreement() -> None:
    """**予想の外の答えを突き返さない。**どちらも選ばず、人に示す形で残す。

    60 周目に測ったとおり、**読みがずれているときに拒まれるのは人のほう**だった。
    仕組みの候補は読み取りから作った予想にすぎないので、人の答えを捨てない。
    """
    engine = KillerQuestionEngine(_with_a_dependent())

    session = engine.run(lambda q: 999)

    assert session.answered == ()
    disagreement = next(d for d in session.disagreements if d.variable == "count")
    assert disagreement.answer == 999
    assert 999 not in disagreement.candidate_values
    # **同じ問いを何度も聞き直さない。**人に回ったものとして残す。
    assert "count" in session.remaining_unresolved
    assert session.stopped_reason == "disagreement"


def test_the_disagreement_does_not_pin_the_value() -> None:
    """食い違いとして残した答えを、そのまま値にしない。"""
    engine = KillerQuestionEngine(_with_a_dependent())

    session = engine.run(lambda q: 999)
    solution = session.final_result.variables["count"]

    assert solution.solved_range == (3, 5)
