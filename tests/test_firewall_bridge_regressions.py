"""`killer_question/firewall_bridge.py` の2つのバグに対する回帰テスト。

**現在の277件のテストは、このどちらのバグも検出できていなかった。**
このファイルは、修正前のコードで確実に落ちることを目的に書いている。

バグ①: 強い軸が1つも無いとき、全証拠のレンジの和集合を
        ``strength="strong"`` のハード変数として登録していた。棄権した証拠の
        番兵値 ``(0, 0)`` と、単位の違う弱い軸のレンジが混ざり、ハード制約の
        範囲を書き換えていた(実測: ``lower=0 upper=246``)。
バグ②: 分岐条件が ``decision.confirmed_range is None`` だったため、階層3
        (要確認)の要素がレンジ幅0の変数として登録され、キラークエスチョンの
        対象から外れ、確定済み金額にも計上されていた。

詳細と実測結果は `docs/top_priority_unit_safety_defect.md` 3-3節・3-4節。

修正前のコード(commit e0bef62)を別 worktree に取り出して実測した結果::

    【バグ①】lower=0 upper=246 strength=strong
             (棄権軸が下限を0に、単位違いの弱い軸が上限を246にした)
    【バグ②】軸名 = firewall_provisional   (階層3なのに階層2の名前)
             confirmed_amount = 40000.0    (期待は 0.0)
             coverage = 1.0                (期待は 0.0)
             next_question = None          (期待は質問が返ること)

このファイルのテストは、上の各値の**逆**を検証している。
"""

from __future__ import annotations

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from killer_question.engine import KillerQuestionEngine
from killer_question.firewall_bridge import (
    FIREWALL_CONFIRMED_AXIS,
    FIREWALL_PROVISIONAL_AXIS,
    FIREWALL_UNCONFIRMED_AXIS,
    add_target_to_joint_solver,
)
from killer_question.precision_mode import PrecisionMode

TARGET = "door_count"


def _strong(count_range: tuple[int, int], *, source: str = "drawing-A") -> AxisEvidence:
    return AxisEvidence(
        derivation="read",
        unit="count",
        target=TARGET, count_range=count_range, source_id=source,
        axis_id="image", method_id=f"detector_{source}", calibrated=True,
    )


def _weak(count_range: tuple[int, int], *, source: str, axis: str = "history") -> AxisEvidence:
    return AxisEvidence(
        derivation="read",
        unit="count",
        target=TARGET, count_range=count_range, source_id=source,
        axis_id=axis, method_id=f"prior_{source}", strength="weak", calibrated=True,
    )


def _abstained(source: str = "mlit") -> AxisEvidence:
    """棄権した証拠。``count_range`` は番兵値で、意味を持たない。"""
    return AxisEvidence(
        derivation="read",
        unit="count",
        target=TARGET, count_range=(0, 0), source_id=source,
        axis_id="history", method_id="industry_statistics",
        strength="weak", status="abstained", calibrated=False,
    )


# =====================================================================
# バグ①: 棄権した軸・単位違いの軸がハード変数の範囲を書き換える
# =====================================================================


def test_bug1_no_strong_axis_never_narrows_anything() -> None:
    """実測校正を通った強い軸が1つも無い読みに、他を狭める力を与えない。

    修正前(commit e0bef62)はここで ``lower=0 upper=246`` の
    ``strength="strong"`` 変数が登録されていた。棄権軸の番兵値が下限を 0 に、
    単位違いの弱い軸が上限を 246 にしていた。**その範囲がハード制約として
    他の要素を狭めるのが実害だった。**

    **2026-09-22 に主張を2回変えたので、経緯を残す。**

    1. 元は「変数を作らないこと」を主張していた。しかし変数が無いと
       **その要素を参照する群合計制約が書けず、同じ群の他の要素の矛盾検出が
       まとめて消えていた**(`docs/group_total_masking_design.md`)。
    2. そこで「読み取り値が支持する狭い範囲で登録する」ことにしたが、実測で
       **停止した要素の誤った読みがクラスタの等式を通って伝播し、他の要素を
       誤った値に確定させた**(19要素のシナリオで精度 0.8947 → 0.6842)。

    いまは方向で分けている。**矛盾を見つける方向**(外れたら人に回る)には
    読み取り値の狭い範囲を使い、**他を狭める方向**(黙って確定する)には
    何も主張させない。このテストはその両方を押さえる。
    """
    decision = AxisQualityFirewall().assess([
        _weak((3, 5), source="自社実績DB"),
        _weak((3, 246), source="mlit-survey", axis="rules"),
        _abstained(),
    ])
    assert decision.confirmed_range is None, "前提: 強い軸が無いので確定範囲も無い"

    solver = ConsistencySolver()
    result = add_target_to_joint_solver(
        solver, TARGET, decision, [_weak((3, 5), source="自社実績DB"), _abstained()]
    )

    assert result.registered is True, "群合計制約を書けるよう、変数自体は作る"
    assert result.escalated_to_review is True, "確認待ちであることは変わらない"
    assert result.requires_confirmation is True

    # 矛盾を見つける方向: 読み取り値の狭い範囲。棄権の番兵値も単位違いも入らない。
    detection = solver.variable_detection_range(TARGET)
    assert detection == (3, 5), f"棄権した証拠や単位違いの軸が混ざった: {detection}"
    assert detection[0] != 0, "番兵値 (0,0) が下限を 0 まで引き下げている"
    assert detection[1] != 246, "単位違いの弱い軸が上限を書き換えている"

    # 他を狭める方向: 何も排除しない。等式で結んだ相手の範囲が変わらないこと
    # で確かめる(範囲の数字そのものではなく、**力を持たないこと**が要件)。
    solver.add_variable("other", 3, 9, axis="image", unit="count")
    solver.add_relation("same_count", TARGET, "==", "other")
    solved = solver.solve().variables["other"].solved_range
    assert solved == (3, 9), (
        f"校正を通っていない読みが、他の要素の範囲を狭めた: {solved}"
    )


def test_bug1_no_strong_axis_still_detects_a_contradiction() -> None:
    """ただし矛盾は見つけられること。狭める力を外しても検出力は残す。

    ``detection_range`` は読み取り値の狭い範囲なので、等式で結んだ相手が
    その範囲とまったく重ならなければ unsat になる。**外れたら人に回る方向**
    にだけ効かせる、という設計の確認。
    """
    decision = AxisQualityFirewall().assess([
        _weak((3, 5), source="自社実績DB"),
        _abstained(),
    ])
    solver = ConsistencySolver()
    add_target_to_joint_solver(
        solver, TARGET, decision, [_weak((3, 5), source="自社実績DB"), _abstained()]
    )
    solver.add_variable("other", 20, 22, axis="image", unit="count")
    solver.add_relation("same_count", TARGET, "==", "other")

    assert solver.solve().status == "sat", "狭める方向には効かせない"
    assert solver.solve(use_detection_ranges=True).status == "unsat", (
        "矛盾を見つける方向には効かせる"
    )


def test_bug1_mixed_units_still_create_no_hard_variable() -> None:
    """単位が混在した証拠からは、いまも変数を作らない。

    違う物差しの数字を重ねた範囲はハード制約にできない(v8 3-2節)。
    バグ①の上限 246 は、まさに金額のレンジが個数に混ざったものだった。
    """
    evidences = [
        _strong((4, 5)),
        AxisEvidence(
            derivation="read", unit="yen",
            target=TARGET, count_range=(30_000, 2_460_000), source_id="mlit-survey",
            axis_id="rules", method_id="industry_statistics",
            strength="weak", calibrated=True,
        ),
    ]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.escalation is not None
    assert decision.escalation.failure_type == "unit_mismatch"

    solver = ConsistencySolver()
    result = add_target_to_joint_solver(solver, TARGET, decision, evidences)

    assert result.registered is False
    assert result.escalated_to_review is True
    assert TARGET not in solver.variable_names()
    assert result.skipped_reason is not None


def test_bug1_an_abstaining_axis_never_widens_a_hard_range() -> None:
    """棄権した軸の番兵値 (0,0) が、ハード変数の下限を 0 まで広げないこと。

    **注: これは修正前のコードでも通る(強い軸があるので確定範囲が使われた)。**
    バグの検出ではなく、将来の退行を防ぐためのガードとして置いている。
    実際にバグを検出するのは、この上下の
    ``test_bug1_no_strong_axis_does_not_create_a_hard_variable`` と
    ``test_bug1_abstained_evidence_is_excluded_even_when_the_union_is_allowed``。
    """
    evidences = [_strong((4, 5)), _abstained()]
    decision = AxisQualityFirewall().assess(evidences)

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)

    solved = solver.solve().variables[TARGET].solved_range
    assert solved == (4, 5), f"棄権した軸が範囲を変えた: {solved}"
    assert solved[0] != 0


def test_bug1_abstained_evidence_is_excluded_even_when_the_union_is_allowed() -> None:
    """暫定の定義域を明示的に許可した場合でも、棄権した証拠は混ぜない。"""
    evidences = [
        _weak((4, 6), source="自社実績DB"),
        _abstained(),
    ]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.confirmed_range is None

    solver = ConsistencySolver()
    result = add_target_to_joint_solver(
        solver, TARGET, decision, evidences, allow_provisional_domain=True
    )

    assert result.registered is True
    solved = solver.solve().variables[TARGET].solved_range
    assert solved == (4, 6), f"棄権した軸の (0,0) が混ざった: {solved}"


def test_bug1_precise_mode_reopening_also_excludes_abstained_evidence() -> None:
    """精密モードで階層2を開き直すときも、棄権した証拠は混ぜない。"""
    evidences = [
        _strong((5, 5), source="strong-src"),
        _weak((4, 6), source="weak-1"),
        _weak((4, 6), source="weak-2", axis="rules"),
        _abstained(),
    ]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.action == "provisional_audit"

    solver = ConsistencySolver()
    add_target_to_joint_solver(
        solver, TARGET, decision, evidences, mode=PrecisionMode.PRECISE
    )

    solved = solver.solve().variables[TARGET].solved_range
    assert solved == (4, 6), f"棄権した軸の (0,0) が混ざった: {solved}"


def test_bug1_a_provisional_domain_is_still_never_treated_as_confirmed() -> None:
    """暫定の定義域を許可しても、確定済みとしては扱われない。"""
    evidences = [_weak((4, 6), source="自社実績DB")]
    decision = AxisQualityFirewall().assess(evidences)

    solver = ConsistencySolver()
    result = add_target_to_joint_solver(
        solver, TARGET, decision, evidences, allow_provisional_domain=True
    )

    assert result.requires_confirmation is True
    assert solver.requires_confirmation(TARGET) is True


# =====================================================================
# バグ②: 階層3の要素が確定済みとして扱われる
# =====================================================================


def test_bug2_tier3_is_not_labelled_as_tier2() -> None:
    """階層3の要素に、階層2の軸名(firewall_provisional)を付けないこと。"""
    evidences = [_strong((4, 4))]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range == (4, 4), "前提: 幅0でも階層3になりうる"

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)

    axis = solver.variable_axis(TARGET)
    assert axis == FIREWALL_UNCONFIRMED_AXIS
    assert axis != FIREWALL_PROVISIONAL_AXIS
    assert axis != FIREWALL_CONFIRMED_AXIS


def test_bug2_a_zero_width_tier3_element_is_still_asked_about() -> None:
    """レンジ幅0の階層3の要素が、キラークエスチョンの対象から外れないこと。

    修正前は幅0を無条件に「確定済み」と見なしていたため、人の確認が必要な
    要素が質問されないまま通っていた。
    """
    evidences = [_strong((4, 4))]
    decision = AxisQualityFirewall().assess(evidences)

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.PRECISE)

    question = engine.next_question()
    assert question is not None, "階層3の要素が質問対象から外れている"
    assert question.variable == TARGET


def test_bug2_a_zero_width_tier3_element_is_not_counted_as_confirmed_money() -> None:
    """階層3の要素の金額が「確定済み金額」に計上されないこと。

    修正前は confirmed_amount = 40000.0(= 単価10000 × 4)が計上され、
    カバレッジが 100% と報告されていた。
    """
    evidences = [_strong((4, 4))]
    decision = AxisQualityFirewall().assess(evidences)

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)
    engine = KillerQuestionEngine(solver, unit_prices={TARGET: 10_000.0})
    result = engine.solver.solve()

    assert engine.estimated_total_amount(result) == 40_000.0
    assert engine.confirmed_amount(result) == 0.0, "未確認の金額が確定済みに計上された"
    assert engine.coverage(result) == 0.0


def test_bug2_tier1_elements_are_still_confirmed_immediately() -> None:
    """階層1(独立した強い軸2つの一致)は、従来どおり即確定すること。

    バグ②の修正が、正当な自動確定まで止めていないことの確認。
    """
    evidences = [_strong((4, 4), source="drawing-A"), _strong((4, 4), source="spec-A")]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.tier == 1

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)
    engine = KillerQuestionEngine(solver, unit_prices={TARGET: 10_000.0})
    result = engine.solver.solve()

    assert solver.variable_axis(TARGET) == FIREWALL_CONFIRMED_AXIS
    assert solver.requires_confirmation(TARGET) is False
    assert engine.confirmed_amount(result) == 40_000.0
    assert engine.coverage(result) == 1.0
    assert engine.next_question() is None


def test_bug2_answering_clears_the_confirmation_requirement() -> None:
    """回答を得た要素は確認済みになり、質問ループが終わること。

    フラグを降ろし忘れると永久に未解決のままになり、``run()`` が安全上限まで
    回ってしまう。
    """
    evidences = [_strong((4, 6))]
    decision = AxisQualityFirewall().assess(evidences)

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)
    engine = KillerQuestionEngine(solver, mode=PrecisionMode.PRECISE)

    session = engine.run(lambda q: 5)

    assert session.question_count == 1
    assert session.stopped_reason == "all_resolved"
    assert session.remaining_unresolved == ()
    assert engine.solver.requires_confirmation(TARGET) is False


def test_bug2_tier2_stays_confirmed_in_standard_mode() -> None:
    """階層2は標準モードでは確定扱いのまま(抜き取り監査で担保する)。"""
    evidences = [
        _strong((5, 5), source="strong-src"),
        _weak((4, 6), source="weak-1"),
        _weak((4, 6), source="weak-2", axis="rules"),
    ]
    decision = AxisQualityFirewall().assess(evidences)
    assert decision.action == "provisional_audit"

    solver = ConsistencySolver()
    add_target_to_joint_solver(solver, TARGET, decision, evidences)

    assert solver.requires_confirmation(TARGET) is False
    assert solver.variable_axis(TARGET) == FIREWALL_PROVISIONAL_AXIS


def test_the_bridge_refuses_an_inconsistent_decision() -> None:
    """action と confirmed_range が矛盾する入力は、黙って通さない。"""

    class _Fake:
        action = "auto_confirm"
        tier = 1
        confirmed_range = None
        escalation = None

    with pytest.raises(ValueError):
        add_target_to_joint_solver(
            ConsistencySolver(), TARGET, _Fake(), [_strong((4, 4))]  # type: ignore[arg-type]
        )
