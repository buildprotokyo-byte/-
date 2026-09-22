"""群合計制約(建具表の「合計30本」など)を、検査として扱うための層。

------------------------------------------------------------------------------
なぜ別のモジュールが要るのか
------------------------------------------------------------------------------
群合計は ``ConsistencySolver.add_relation`` でも書けるが、それだけでは
**検査として成立しない**。2026-09-22 の実測で分かったのは次の2点である
(`docs/group_total_masking_design.md`)。

1. 群の中の1要素が自由に動けるだけで、群合計は何も否定できなくなる。
   合計が合わないぶんを、その要素が全部吸収してしまうため。
2. **sat は「誤りが無い」を意味しない。** ある要素の値を別の値に変えても
   群合計が sat のままなら、**その群合計はその要素について何も確かめていない。**
   いまの経路では、この区別をどこでも問うていなかった。

このモジュールは、群合計を「宣言する入口」(:class:`GroupTotalConstraint`)と、
「その検査が実際に何を確かめたのかを問う関数」(:func:`check_group_total`)を
提供する。**要素の確信度階層を上げることはしない。** 群合計ができるのは、
矛盾を見つけて群を止めることと、「この要素については何も確かめていない」と
申告することだけである。

------------------------------------------------------------------------------
判定の手順
------------------------------------------------------------------------------
``check_group_total`` は次の順で判定する。**どの solve も
``use_detection_ranges=True``**、つまり読み取り値に基づく狭い範囲を使う。
人への質問のために広げた範囲を使うと、広がった幅が他の要素の誤りを吸収する。

1. 群合計を含めて解く。**unsat なら群を止める**(安全装置。ここは弱めない)。
2. sat なら、要素ごとに「群合計がこの要素を確かめたか」を調べる。
   群合計**あり**で値が1つに決まり、群合計**なし**では決まらない要素だけが
   「確かめられた」。自分の読みだけで既に幅0だった要素は、群合計からは
   何も得ていない(``unverified_targets``)。
3. 停止した要素(``requires_confirmation=True``)が、群合計のせいで自分の
   読みの中心から :data:`~arbitration.axis_quality_firewall.CENTER_TOLERANCES`
   を超えて押し出されていれば、その sat は「誤りが無い」ではなく
   **「停止した要素が吸収した」**である(``absorbing_targets``)。
4. 吸収が起きた群では、群の中の階層1(自動確定)の要素を
   ``targets_requiring_audit`` に挙げる。呼び出し側はこれを階層2
   (仮採用+抜き取り監査)へ落とす。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Sequence

from arbitration.axis_quality_firewall import (
    CENTER_TOLERANCES,
    UNKNOWN_UNIT_CENTER_TOLERANCE,
    CenterTolerance,
)
from arbitration.consistency_solver import ConsistencySolver, IntRange


@dataclass(frozen=True)
class GroupTotalConstraint:
    """「この要素たちの合計はこの値である」という、群にひとつの制約。

    ``members`` は同じ単位・同じ粒度の要素でなければならない
    (v8 3-2節。違う物差しの数字は足せない)。``unit`` は中心値の許容差を
    引くために使う。
    """

    name: str
    members: tuple[str, ...]
    total: int
    unit: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("群合計制約には名前が必要です")
        if len(self.members) < 2:
            raise ValueError("群合計制約には2要素以上が必要です")
        if len(set(self.members)) != len(self.members):
            raise ValueError(f"members に同じ要素が重複しています: {self.members}")

    def apply(self, solver: ConsistencySolver) -> None:
        """この群合計を solver に宣言する。

        **群の要素が1つでも登録されていなければ、その場で失敗させる。**
        黙って読み飛ばすと「制約があるのに何も検査していない」状態になり、
        それが 2026-09-22 に見つかった欠陥そのものだった。
        """
        missing = [name for name in self.members if not solver.has_variable(name)]
        if missing:
            raise KeyError(
                f"群合計制約 '{self.name}' の要素が solver に登録されていません: "
                f"{missing}。停止した要素も変数として登録してください"
            )
        members = self.members
        total = self.total
        solver.add_relation(
            self.name,
            lambda variables: sum(variables[name] for name in members),
            "==",
            total,
            description=self.description or f"{len(members)}要素の合計 = {total}",
        )


@dataclass(frozen=True)
class GroupTotalCheck:
    """:func:`check_group_total` の結果。"""

    constraint_name: str
    status: Literal["sat", "unsat"]
    #: 群合計が値を1つに絞った要素(= 群合計が実際に確かめた要素)。
    verified_targets: tuple[str, ...]
    #: 群合計がその要素について何も確かめていない要素。
    #: **この要素の自動確定の根拠に、群合計が通ったことを数えてはならない。**
    unverified_targets: tuple[str, ...]
    #: 群合計を成立させるために、自分の読みの中心から許容差を超えて
    #: 押し出された停止要素。
    absorbing_targets: tuple[str, ...]
    #: 吸収が起きた群の中で、自動確定のままにしてはならない要素。
    targets_requiring_audit: tuple[str, ...]
    conflicting_constraints: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def is_absorbed(self) -> bool:
        """停止した要素の余裕で吸収されて sat になったか。"""
        return bool(self.absorbing_targets)

    @property
    def stops_the_group(self) -> bool:
        """群を止めるべきか(矛盾したか、吸収が起きたか)。"""
        return self.status == "unsat" or self.is_absorbed


def _tolerance_for(unit: str) -> tuple[CenterTolerance, bool]:
    found = CENTER_TOLERANCES.get(unit)
    if found is not None:
        return found, False
    return UNKNOWN_UNIT_CENTER_TOLERANCE, True


def _center_window(count_range: IntRange, unit: str) -> tuple[Fraction, Fraction]:
    """その範囲の中心 ± 許容差。中心は ``Fraction`` で厳密に持つ。

    float にすると 0.5 や 5% が正確に表せず、境界の判定が入力によって揺れる
    (``axis_quality_firewall`` の中心値検査と同じ理由)。
    """
    center = Fraction(count_range[0] + count_range[1], 2)
    tolerance, _ = _tolerance_for(unit)
    if tolerance.absolute is not None:
        width = Fraction(tolerance.absolute)
    else:
        assert tolerance.relative is not None  # CenterTolerance が保証する
        width = tolerance.relative * abs(center)
    return (center - width, center + width)


def _is_pinned(solver: ConsistencySolver, target: str) -> bool | None:
    """矛盾検出用の範囲で解いたとき、その要素の値が1つに決まるか。

    ``None`` は「解が無い」(unsat)。
    """
    result = solver.solve(use_detection_ranges=True)
    if result.status == "unsat":
        return None
    solution = result.variables.get(target)
    if solution is None:
        return False
    return solution.solved_range[0] == solution.solved_range[1]


def check_group_total(
    solver: ConsistencySolver,
    constraint: GroupTotalConstraint,
    *,
    auto_confirmed_tier: int = 1,
) -> GroupTotalCheck:
    """群合計制約が実際に何を確かめたのかを判定する。

    ``solver`` には既に ``constraint.apply(solver)`` が済んでいること。
    ``auto_confirmed_tier`` は「自動確定」とみなす確信度階層の番号で、
    ``firewall_bridge`` が変数の根拠に入れる ``firewall_tier`` と突き合わせる。
    """
    missing = [name for name in constraint.members if not solver.has_variable(name)]
    if missing:
        raise KeyError(
            f"群合計制約 '{constraint.name}' の要素が solver にありません: {missing}"
        )
    if constraint.name not in solver.constraint_names():
        raise KeyError(
            f"群合計制約 '{constraint.name}' が solver に宣言されていません。"
            "先に GroupTotalConstraint.apply() を呼んでください"
        )

    reasons: list[str] = []
    with_total = solver.solve(use_detection_ranges=True)

    if with_total.status == "unsat":
        # **安全装置。ここは弱めない。** 群合計が矛盾したということは、
        # 読み取り値のどこかが確実に誤っている。群ごと人へ回す。
        reasons.append(
            f"群合計({constraint.total})が読み取り値と矛盾したため、群を止める"
        )
        return GroupTotalCheck(
            constraint_name=constraint.name,
            status="unsat",
            verified_targets=(),
            unverified_targets=tuple(constraint.members),
            absorbing_targets=(),
            targets_requiring_audit=tuple(constraint.members),
            conflicting_constraints=with_total.conflicting_constraints,
            reasons=tuple(reasons),
        )

    without_total = _solver_without(solver, constraint.name)
    baseline = without_total.solve(use_detection_ranges=True)

    verified: list[str] = []
    unverified: list[str] = []
    absorbing: list[str] = []

    for name in constraint.members:
        pinned_with = with_total.variables[name].solved_range
        pinned_without = baseline.variables[name].solved_range
        gained = (
            pinned_with[0] == pinned_with[1] and pinned_without[0] != pinned_without[1]
        )
        if gained:
            verified.append(name)
        else:
            unverified.append(name)

        if not solver.requires_confirmation(name):
            continue
        # 停止した要素が、自分の読みの中心からどれだけ押し出されたか。
        low, high = _center_window(solver.variable_detection_range(name), constraint.unit)
        if pinned_with[1] < low or pinned_with[0] > high:
            absorbing.append(name)

    if unverified:
        reasons.append(
            "群合計は次の要素について何も確かめていない(自動確定の根拠に数えない): "
            + ", ".join(unverified)
        )
    requiring_audit: tuple[str, ...] = ()
    if absorbing:
        reasons.append(
            "群合計は、停止した要素("
            + ", ".join(absorbing)
            + ")を自分の読みの中心から許容差を超えて押し出すことで成立している。"
            "この sat は「誤りが無い」ではなく「停止した要素が吸収した」である"
        )
        requiring_audit = tuple(
            name
            for name in constraint.members
            if name not in absorbing
            and solver.variable_evidence(name).get("firewall_tier") == auto_confirmed_tier
        )
        if requiring_audit:
            reasons.append(
                "吸収が起きた群なので、次の自動確定を仮採用(抜き取り監査)へ落とす: "
                + ", ".join(requiring_audit)
            )

    return GroupTotalCheck(
        constraint_name=constraint.name,
        status="sat",
        verified_targets=tuple(verified),
        unverified_targets=tuple(unverified),
        absorbing_targets=tuple(absorbing),
        targets_requiring_audit=requiring_audit,
        conflicting_constraints=(),
        reasons=tuple(reasons),
    )


def _solver_without(solver: ConsistencySolver, constraint_name: str) -> ConsistencySolver:
    """指定した制約だけを外した複製を作る(元の solver は変えない)。"""
    clone = solver.clone()
    clone.remove_constraint(constraint_name)
    return clone


def group_total_is_meaningless(
    solver: ConsistencySolver, constraint: GroupTotalConstraint
) -> bool:
    """その群合計が、そもそも何も否定できない状態かどうか。

    停止した要素の幅の合計(吸収余地)が、群の中で最も小さい単位の差を
    上回っていれば、どんな1要素分の誤りも吸収できてしまう。**「検査あり」と
    数えてよいかの判断に使う**(`arbitration/provisional_audit.py` が
    照合できない対象を ``hit_rate=None`` にするのと同じ考え方)。
    """
    slack = sum(
        upper - lower
        for lower, upper in (
            solver.variable_detection_range(name)
            for name in constraint.members
            if solver.requires_confirmation(name)
        )
    )
    return slack >= 1


def collect_group_members(
    solver: ConsistencySolver, targets: Sequence[str]
) -> tuple[str, ...]:
    """solver に登録済みの要素だけを、渡された順で返す(欠落の確認用)。"""
    return tuple(name for name in targets if solver.has_variable(name))
