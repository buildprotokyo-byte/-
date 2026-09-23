"""群合計制約の矛盾検出が、停止した要素のせいで効かなくなる問題の回帰テスト。

**下の3件は、2026-09-22 の修正前には落ちるテストとして先に書いたものである**
(当時は ``xfail(strict=True)`` を付けていた)。修正後の現在は印を外して
通常のテストとして通る。修正を戻すと再び落ちる。

現象(Codex がトライアル16の保存データを commit c0c9d13 の本番ファイア
ウォールで再判定して報告したもの)を、**このリポジトリの本番経路**
(``AxisQualityFirewall`` → ``killer_question.firewall_bridge`` →
``ConsistencySolver``)で組み直したもの。

------------------------------------------------------------------------------
何が起きるか
------------------------------------------------------------------------------
群合計制約(建具表の「合計30本」のような、群全体に1つだけ与えられる等式)は、
**群の中の1要素が自由に動けるだけで、何も否定できなくなる。** 合計が合わない
ぶんを、その要素が全部吸収してしまうため。

本番ファイアウォールは階層2に「強い軸1つ + 異なる弱いデータ源2つ以上」を
要求するので、支持の足りない要素は階層3(要確認)で停止する。修正前、停止した要素は

- ``confirmed_range`` が ``None`` なら **変数として登録されなかった**
  (``firewall_bridge`` のバグ①修正の副作用)。呼び出し側は
  その要素を参照する群合計制約を**立てられない**(``KeyError`` になる)ので、
  群まるごと検査が消えていた。
- 精密モードや ``allow_provisional_domain=True`` では、**読みの和集合まで
  範囲が開き直されていた。** 幅が広がったぶんが吸収の余地になる。

どちらの場合も、群合計制約は unsat から sat に変わっていた。すると
**「強い2軸が同じ誤った値で一致する」型の誤り**(積集合が空にならないので
矛盾として検出できず、中心値も一致するので中心値検査も発火しない)が、
階層1で自動確定したまま誰にも見られずに通る。この型の誤りは、要素単体の
検査では原理的に捕まえられず、**群合計制約だけが唯一の検出経路**だった。

------------------------------------------------------------------------------
テストが主張していること
------------------------------------------------------------------------------
「停止した要素があっても群合計制約で誤りを検出できること」そのものは、
要求として強すぎる(停止した要素の幅が十分に大きければ、どんな誤りも
吸収できてしまうのは論理的に避けられない)。そこで主張は2つに分ける。

1. **群合計制約を立てられること。** 停止した要素が変数として登録されず、
   制約を書くことすらできない、という状態にはしない。
2. **「停止した要素が吸収したから sat になった」を、sat と区別すること。**
   吸収が起きた群では、群の中の階層1の要素を自動確定のままにしない。

設計と実装は ``docs/group_total_masking_design.md``、
``arbitration/group_total.py``、``arbitration/consistency_solver.py`` の
``Variable.detection_range``。
"""

from __future__ import annotations

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver, SolveResult
from arbitration.group_total import GroupTotalConstraint, check_group_total
from killer_question.firewall_bridge import add_target_to_joint_solver
from killer_question.precision_mode import PrecisionMode

#: 同じ単位の10要素からなる群。建具表に「合計30本」と書かれている想定。
GROUP = tuple(f"door_{index}" for index in range(10))
TRUTH = {name: 3 for name in GROUP}
GROUP_TOTAL = sum(TRUTH.values())


def _evidence(
    target: str,
    count_range: tuple[int, int],
    source_id: str,
    axis_id: str,
    *,
    strength: str = "strong",
    calibrated: bool = True,
) -> AxisEvidence:
    return AxisEvidence(
        target=target,
        count_range=count_range,
        source_id=source_id,
        axis_id=axis_id,
        method_id=f"method_{source_id}",
        unit="count",
        derivation="read",
        strength=strength,  # type: ignore[arg-type]
        calibrated=calibrated,
    )


def _agreeing_strong_axes(target: str, value: int) -> list[AxisEvidence]:
    """独立した強い軸2つが、**同じ値**で一致している状態。

    ``value`` が誤っていても、積集合は空にならず(矛盾として検出できない)、
    中心値も完全に一致する(中心値検査も発火しない)。要素単体の検査で
    捕まえられないのはこの形である。
    """
    return [
        _evidence(target, (value, value), "drawing-A", "image"),
        _evidence(target, (value, value), "spec-A", "text"),
    ]


def _build_group(
    *,
    stopped_evidence: list[AxisEvidence],
    wrong_targets: dict[str, int],
) -> dict[str, list[AxisEvidence]]:
    """``door_0`` だけ ``stopped_evidence`` を使い、残りは強い2軸が一致する群。"""
    evidence: dict[str, list[AxisEvidence]] = {"door_0": stopped_evidence}
    for name in GROUP[1:]:
        evidence[name] = _agreeing_strong_axes(
            name, wrong_targets.get(name, TRUTH[name])
        )
    return evidence


def _run_firewall(
    evidence: dict[str, list[AxisEvidence]],
    *,
    mode: PrecisionMode = PrecisionMode.STANDARD,
    allow_provisional_domain: bool = False,
) -> tuple[ConsistencySolver, dict[str, object], list[str]]:
    """本番経路どおり、要素ごとにファイアウォールへ通して結合solverへ入れる。"""
    firewall = AxisQualityFirewall()
    solver = ConsistencySolver()
    decisions: dict[str, object] = {}
    unregistered: list[str] = []
    for name, items in evidence.items():
        decision = firewall.assess(items)
        decisions[name] = decision
        result = add_target_to_joint_solver(
            solver,
            name,
            decision,
            items,
            mode=mode,
            allow_provisional_domain=allow_provisional_domain,
        )
        if not result.registered:
            unregistered.append(name)
    return solver, decisions, unregistered


def _add_group_total(solver: ConsistencySolver) -> GroupTotalConstraint:
    """群合計制約(建具表の「合計30本」)を宣言する。

    群の要素が1つでも solver に登録されていないと ``KeyError`` になる。
    **「制約を立てられるか」自体が検査対象**なので、存在しない変数を黙って
    読み飛ばさない。
    """
    constraint = GroupTotalConstraint(
        name="group_total",
        members=GROUP,
        total=GROUP_TOTAL,
        unit="count",
        description="建具表の群合計(合計30本)",
    )
    constraint.apply(solver)
    return constraint


def _detect(solver: ConsistencySolver) -> SolveResult:
    """矛盾検出。**読み取り値に基づく狭い範囲**で解く。

    人への質問のために広げた範囲で解くと、広がった幅が他の要素の誤りを
    吸収してしまう。矛盾を見つけたいときはこちらを使う。
    """
    return solver.solve(use_detection_ranges=True)


# ---------------------------------------------------------------------------
# 前提の確認(いまも通る)。壊れているのが「停止したとき」だけであることを示す。
# ---------------------------------------------------------------------------


def test_the_group_total_catches_two_strong_axes_agreeing_on_a_wrong_value() -> None:
    """誰も停止しなければ、群合計制約はこの型の誤りを unsat として捕まえる。

    ``door_1`` / ``door_2`` は独立した強い軸2つが同じ誤値(1、正解3)で一致
    するため、要素単体では階層1で自動確定する。群合計だけが唯一の検出経路。
    """
    evidence = _build_group(
        stopped_evidence=_agreeing_strong_axes("door_0", TRUTH["door_0"]),
        wrong_targets={"door_1": 1, "door_2": 1},
    )
    solver, decisions, unregistered = _run_firewall(evidence)

    assert unregistered == []
    assert decisions["door_1"].tier == 1  # type: ignore[attr-defined]
    assert decisions["door_2"].tier == 1  # type: ignore[attr-defined]

    _add_group_total(solver)
    result = _detect(solver)

    assert result.status == "unsat", "群合計制約が誤りを検出できていない"
    assert "group_total" in result.conflicting_constraints


def test_a_narrow_stopped_element_still_leaves_the_group_total_effective() -> None:
    """停止していても幅が狭ければ検出は残る。壊すのは「幅」であることの確認。

    ``door_0`` は階層3で停止するが、範囲は強い軸の読み ``(2, 4)`` のままなので
    吸収できるのは ±1 まで。誤差4は吸収しきれず unsat のままになる。
    """
    evidence = _build_group(
        stopped_evidence=[_evidence("door_0", (2, 4), "drawing-A", "image")],
        wrong_targets={"door_1": 1, "door_2": 1},
    )
    solver, decisions, unregistered = _run_firewall(evidence)

    assert decisions["door_0"].tier == 3  # type: ignore[attr-defined]
    assert unregistered == []
    _add_group_total(solver)

    assert _detect(solver).status == "unsat"


# ---------------------------------------------------------------------------
# 修正前に落ちていた3件
# ---------------------------------------------------------------------------


def test_a_stopped_element_without_a_strong_axis_keeps_the_group_total_checkable() -> None:
    """強い軸が1つも無くても、群合計制約を立てられなければならない。

    修正前、``firewall_bridge`` はこの要素を ``registered=False`` で返していた
    (バグ①の修正。棄権した証拠の番兵値がハード制約の範囲を書き換えるのを
    防ぐため)。安全側の判断としては正しいが、**群合計制約から見ると、その要素
    だけでなく群全体の検査が消えていた。** 停止した1要素を守るために、同じ群の
    他の9要素の誤りが見えなくなる。

    修正後は、読み取り値が支持する狭い範囲 ``(2, 4)`` で登録される。棄権した
    証拠の番兵値は混ざらない。
    """
    evidence = _build_group(
        stopped_evidence=[
            # 実測校正を通っていない読みだけ → 使える強い軸が0
            _evidence("door_0", (2, 4), "drawing-A", "image", calibrated=False),
            _evidence("door_0", (1, 9), "自社実績DB", "history", strength="weak"),
        ],
        wrong_targets={"door_1": 1, "door_2": 1},
    )
    solver, _decisions, unregistered = _run_firewall(evidence)

    assert unregistered == [], (
        "階層3の要素が変数として登録されないため、群合計制約を書けない"
    )
    _add_group_total(solver)
    assert _detect(solver).status == "unsat"


def test_widening_a_stopped_element_must_not_make_the_group_total_satisfiable() -> None:
    """範囲を開き直しても、群合計制約の判定を変えてはならない。

    ``door_0`` は強い軸1つ + 弱いデータ源2つで階層2。標準モードでは範囲が
    ``(2, 4)`` のままで群合計は unsat(= ``door_1`` の誤りを検出できる)だが、
    精密モードでは読みの和集合 ``(0, 6)`` まで開き直されるため、不足分2を
    ``door_0`` が吸収して sat になる。

    **開き直しの目的は「その要素を人に質問できるようにすること」であって、
    群全体の検査を緩めることではない。** 質問の候補を広げる話と、共有solver
    の定義域を広げる話が、同じ1つの範囲に同居しているのが原因。
    """
    stopped = [
        _evidence("door_0", (2, 4), "drawing-A", "image"),
        _evidence("door_0", (1, 5), "自社実績DB", "history", strength="weak"),
        _evidence("door_0", (0, 6), "rule-A", "rules", strength="weak"),
    ]
    wrong = {"door_1": 1}

    standard, decisions, _ = _run_firewall(_build_group(
        stopped_evidence=stopped, wrong_targets=wrong))
    assert decisions["door_0"].tier == 2  # type: ignore[attr-defined]
    _add_group_total(standard)
    assert _detect(standard).status == "unsat", "標準モードでは検出できている"

    precise, _decisions, _ = _run_firewall(
        _build_group(stopped_evidence=stopped, wrong_targets=wrong),
        mode=PrecisionMode.PRECISE,
    )
    _add_group_total(precise)

    assert _detect(precise).status == "unsat", (
        "精密モードで範囲を開き直したために、他の要素の誤りが吸収された"
    )
    # 確定に使う範囲は広がっているが、矛盾検出に使う範囲は据え置かれている。
    assert precise.variable_range("door_0") == (0, 6)
    assert precise.variable_detection_range("door_0") == (2, 4)


def test_the_group_total_must_not_be_satisfied_only_by_the_stopped_elements_slack() -> None:
    """吸収されて sat になった群では、階層1の要素を自動確定のままにしない。

    停止した要素の幅が十分に大きければ、どんな誤りも吸収できてしまうのは
    論理的に避けられない。避けられないからこそ、**「合計が合った」を
    「誤りが無い」と読み替えないこと**が要件になる。

    ここでは停止した要素の**検出用の範囲そのもの**が広い群を作る
    (``door_0`` の読みは全部 ``(0, 6)`` で一致しているので、狭めようがない)。
    このとき群合計は sat になるが、``door_0`` は自分の読みの中心 3 から
    個数の許容差 ±1 を超えて 5 まで押し出されている。
    """
    stopped = [
        _evidence("door_0", (0, 6), "drawing-A", "image"),
        _evidence("door_0", (0, 6), "自社実績DB", "history", strength="weak"),
    ]
    solver, decisions, unregistered = _run_firewall(
        _build_group(stopped_evidence=stopped, wrong_targets={"door_1": 1})
    )
    assert unregistered == []
    assert decisions["door_0"].tier == 3  # type: ignore[attr-defined]
    assert solver.variable_detection_range("door_0") == (0, 6), "前提: 狭めようがない"

    constraint = _add_group_total(solver)
    assert _detect(solver).status == "sat", "前提: 幅が広いので吸収されてしまう"

    check = check_group_total(solver, constraint)

    assert check.status == "sat"
    assert check.is_absorbed, "吸収されたのに sat と区別されていない"
    assert check.absorbing_targets == ("door_0",)
    # 群合計は door_0 を 5 に固定しただけで、door_1 については何も確かめていない。
    assert "door_1" in check.unverified_targets
    # 吸収が起きた群なので、階層1の要素は自動確定のままにしない。
    assert "door_1" in check.targets_requiring_audit
    assert "door_0" not in check.targets_requiring_audit


def test_a_group_total_that_pins_nothing_verifies_nothing() -> None:
    """自分の読みだけで既に幅0の要素について、群合計は何も確かめていない。

    おーちゃんの判定基準: 「ある要素の値を別の値に変えても群合計が sat の
    ままなら、その群合計はその要素について何も確かめていない」。
    幅0の要素は群合計が無くても値が1つなので、群合計からは何も得ていない。
    """
    evidence = _build_group(
        stopped_evidence=_agreeing_strong_axes("door_0", TRUTH["door_0"]),
        wrong_targets={},
    )
    solver, _decisions, _ = _run_firewall(evidence)
    constraint = _add_group_total(solver)

    check = check_group_total(solver, constraint)

    assert check.status == "sat"
    assert check.verified_targets == (), "幅0の要素を「確かめた」と数えている"
    assert set(check.unverified_targets) == set(GROUP)
    assert not check.is_absorbed
    assert check.targets_requiring_audit == ()


def test_an_unsat_group_total_still_stops_the_group() -> None:
    """群合計が矛盾したときに群を止める安全装置は、修正後も弱めない。"""
    evidence = _build_group(
        stopped_evidence=_agreeing_strong_axes("door_0", TRUTH["door_0"]),
        wrong_targets={"door_1": 1, "door_2": 1},
    )
    solver, _decisions, _ = _run_firewall(evidence)
    constraint = _add_group_total(solver)

    check = check_group_total(solver, constraint)

    assert check.status == "unsat"
    assert check.stops_the_group
    assert "group_total" in check.conflicting_constraints
    assert set(check.targets_requiring_audit) == set(GROUP)


def test_the_group_total_can_pin_an_element_it_actually_determines() -> None:
    """群合計が1要素だけ未確定の群を解いたときは、その要素を「確かめた」と数える。"""
    evidence = _build_group(
        stopped_evidence=[_evidence("door_0", (2, 4), "drawing-A", "image")],
        wrong_targets={},
    )
    solver, _decisions, _ = _run_firewall(evidence)
    constraint = _add_group_total(solver)

    check = check_group_total(solver, constraint)

    assert check.status == "sat"
    assert check.verified_targets == ("door_0",)
    assert "door_0" not in check.unverified_targets
    assert not check.is_absorbed, "正しい読みなので押し出されていない"


def test_a_group_total_cannot_confirm_a_stopped_element_by_itself() -> None:
    """群合計が停止した要素の値を1つに絞っても、確定済みにはならない。

    おーちゃんの条件2「群合計が sat になっても、それを要素の自動確定の根拠に
    数えない」の、もう一方の側。群合計は ``door_0`` を 3 に固定するが、
    ``door_0`` は人の確認待ちのままでなければならない。幅0を確定済みと
    同一視するのがバグ②で、その修正がここでも効いていることを押さえる。
    """
    solver, decisions, _ = _run_firewall(
        _build_group(
            stopped_evidence=[_evidence("door_0", (2, 4), "drawing-A", "image")],
            wrong_targets={},
        )
    )
    assert decisions["door_0"].tier == 3  # type: ignore[attr-defined]
    constraint = _add_group_total(solver)

    check = check_group_total(solver, constraint)
    assert check.verified_targets == ("door_0",), "前提: 群合計が値を1つに絞る"

    solved = _detect(solver).variables["door_0"].solved_range
    assert solved == (3, 3), "前提: 幅0まで絞られる"
    assert solver.requires_confirmation("door_0") is True, (
        "群合計で幅0になっただけで確定済みとして扱われている"
    )
    assert "door_0" in solver.names_requiring_confirmation()


def test_the_detection_power_of_a_group_total_is_reported_as_a_number() -> None:
    """群合計の吸収余地を数字で出し、捕まえたい誤差と比べられること。

    停止した要素の幅が十分に大きければ、どんな誤りも吸収できてしまうのは
    論理の帰結である。**隠すのではなく数字で出す**(設計案4-4節)。
    """
    from arbitration.group_total import (
        group_total_absorption_slack,
        group_total_has_detection_power,
    )

    solver, _decisions, _ = _run_firewall(
        _build_group(
            stopped_evidence=[_evidence("door_0", (0, 6), "drawing-A", "image")],
            wrong_targets={},
        )
    )
    constraint = _add_group_total(solver)

    assert group_total_absorption_slack(solver, constraint) == 6
    assert not group_total_has_detection_power(solver, constraint, smallest_error=1)
    assert group_total_has_detection_power(solver, constraint, smallest_error=7)

    tight, _decisions, _ = _run_firewall(
        _build_group(
            stopped_evidence=_agreeing_strong_axes("door_0", TRUTH["door_0"]),
            wrong_targets={},
        )
    )
    tight_constraint = _add_group_total(tight)
    assert group_total_absorption_slack(tight, tight_constraint) == 0, (
        "停止した要素が無ければ吸収余地も無い"
    )
    assert group_total_has_detection_power(tight, tight_constraint, smallest_error=1)
