"""群合計制約の矛盾検出が、停止した要素のせいで効かなくなることを再現する。

**このファイルのテストは、現在の実装では通らない。** 意図した振る舞いを先に
書き、``xfail(strict=True)`` で印を付けてある。実装を直すと XPASS になって
**そのとき落ちる**ので、直した人は印を外すことになる。印を外して実行すると、
いまは下記のとおり実際に落ちる(2026-09-22 実測)。

    tests/test_group_total_masking.py::
      test_a_stopped_element_without_a_strong_axis_keeps_the_group_total_checkable
      test_widening_a_stopped_element_must_not_make_the_group_total_satisfiable
      test_the_group_total_must_not_be_satisfied_only_by_the_stopped_elements_slack

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
要求するので、支持の足りない要素は階層3(要確認)で停止する。停止した要素は

- ``confirmed_range`` が ``None`` なら **変数として登録されない**
  (``firewall_bridge`` のバグ①修正。既定の挙動)。呼び出し側は
  その要素を参照する群合計制約を**立てられない**(``KeyError`` になる)ので、
  群まるごと検査が消える。
- 精密モードや ``allow_provisional_domain=True`` では、**読みの和集合まで
  範囲が開き直される。** 幅が広がったぶんが吸収の余地になる。

どちらの場合も、群合計制約は unsat から sat に変わる。すると
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

設計案は ``docs/group_total_masking_design.md``。
"""

from __future__ import annotations

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
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


def _add_group_total(solver: ConsistencySolver) -> None:
    """群合計制約(建具表の「合計30本」)を宣言する。

    群の要素が1つでも solver に登録されていないと、``solve()`` の中で
    ``KeyError`` になる。**「制約を立てられるか」自体が検査対象**なので、
    ここでは存在しない変数を黙って読み飛ばさない。
    """
    solver.add_relation(
        "group_total",
        lambda variables: sum(variables[name] for name in GROUP),
        "==",
        GROUP_TOTAL,
        description="建具表の群合計(合計30本)",
    )


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
    result = solver.solve()

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

    assert solver.solve().status == "unsat"


# ---------------------------------------------------------------------------
# 再現(いまは落ちる)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "使える強い軸が1つも無い階層3の要素は変数として登録されないため、"
        "その要素を含む群合計制約を宣言すると solve() が KeyError になる。"
        "群まるごと検査が消える"
    ),
)
def test_a_stopped_element_without_a_strong_axis_keeps_the_group_total_checkable() -> None:
    """強い軸が1つも無くても、群合計制約を立てられなければならない。

    ``firewall_bridge`` はこの要素を ``registered=False`` で返す(バグ①の
    修正。棄権した証拠の番兵値がハード制約の範囲を書き換えるのを防ぐため)。
    安全側の判断としては正しいが、**群合計制約から見ると、その要素だけでなく
    群全体の検査が消える。** 停止した1要素を守るために、同じ群の他の9要素の
    誤りが見えなくなる。
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
    assert solver.solve().status == "unsat"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "精密モードは階層2の要素の範囲を読みの和集合まで開き直すため、"
        "広がった幅が他の要素の誤りを吸収して unsat が sat に変わる"
    ),
)
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
    assert standard.solve().status == "unsat", "標準モードでは検出できている"

    precise, _decisions, _ = _run_firewall(
        _build_group(stopped_evidence=stopped, wrong_targets=wrong),
        mode=PrecisionMode.PRECISE,
    )
    _add_group_total(precise)

    assert precise.solve().status == "unsat", (
        "精密モードで範囲を開き直したために、他の要素の誤りが吸収された"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "群合計制約が「停止した要素の余裕で吸収されただけで sat になった」"
        "ことを判別する仕組みが無く、群の中の階層1の要素がそのまま自動確定する"
    ),
)
def test_the_group_total_must_not_be_satisfied_only_by_the_stopped_elements_slack() -> None:
    """吸収されて sat になった群では、階層1の要素を自動確定のままにしない。

    停止した要素の幅が十分に大きければ、どんな誤りも吸収できてしまうのは
    論理的に避けられない。避けられないからこそ、**「合計が合った」を
    「誤りが無い」と読み替えないこと**が要件になる。

    ここでは、停止した要素に残差(合計を成立させるために必要な値)を
    押し付けた結果、その要素自身の中心値から ``CENTER_TOLERANCES`` を
    超えて離れているかどうかで判定できるはずだ、という主張を置いている
    (設計案は ``docs/group_total_masking_design.md`` 4-1節)。
    """
    stopped = [
        _evidence("door_0", (2, 4), "drawing-A", "image"),
        _evidence("door_0", (1, 5), "自社実績DB", "history", strength="weak"),
        _evidence("door_0", (0, 6), "rule-A", "rules", strength="weak"),
    ]
    solver, decisions, _ = _run_firewall(
        _build_group(stopped_evidence=stopped, wrong_targets={"door_1": 1}),
        mode=PrecisionMode.PRECISE,
    )
    _add_group_total(solver)
    result = solver.solve()

    # 合計は成立する(door_0 が 0〜6 の幅で不足分2を吸収する)。
    assert result.status == "sat"

    # だが door_0 が押し付けられた値は、door_0 自身の読みの中心(3)から
    # 個数の許容差 ±1 を超えて離れている。この群は自動確定してはならない。
    from arbitration.axis_quality_firewall import group_total_absorption  # type: ignore[attr-defined]

    absorbed = group_total_absorption(solver, GROUP, GROUP_TOTAL, unit="count")
    assert absorbed.is_absorbed
    assert absorbed.escalated_targets == ("door_1",) or "door_1" in absorbed.escalated_targets
