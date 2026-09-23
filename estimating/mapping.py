"""数量を見積の行に当てはめる。

**この層は当てはめるだけで、判定をやり直さない。** 数量が確定しているか
どうかは仲裁層(`arbitration/`)がすでに出している。ここが足すのは
「当てはめが一意に決まったか」だけで、**確定にはその両方が要る。**

確定の条件(3 つすべて)
----------------------
1. 当たった規則がちょうど 1 つであること(一意に決まった)
2. 数量そのものが仲裁層で確定していること(`action == "auto_confirm"`)
3. その見積の行に、ほかの数量がぶつかっていないこと

1 だけで確定させると「規則がきれいに書けている」という**単一の指標だけ**を
根拠に自動確定することになる。2 だけでも足りない(どの行の数量なのかが
決まっていない)。3 が要るのは、入口が「ページをまたいで足さない」と
決めているためで、既存平面図と新設平面図の開き戸が同じ行に当たったとき、
ここで足すと同じ建具を二重に数える。**足さずに人へ回す。**

2026-09-22 時点では 2 が常に偽である(入口が出す数量は全件が階層3)。
したがって**この経路から出る確定は 0 件**で、出るのは候補だけである。
これは不具合ではなく、`intake/drawing_intake.py` の
`test_nothing_is_auto_confirmed_through_this_path` と同じ事実である。

当てはまらなかった数量は捨てない
--------------------------------
規則が 1 つも当たらなかった数量も `QuantityMapping` として残し、当たらな
かった理由を付ける。黙って消すと、見積に出てこない項目があることに
集計表からは気づけない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Literal, Mapping, Sequence

from arbitration.units import UNIT_ALIASES
from estimating.basis import BASIS_STRONGEST_FIRST, weakest
from estimating.decisive import (
    NOT_OBTAINED_NO_RULE,
    NOT_OBTAINED_OTHER,
    NOT_OBTAINED_SITE_SURVEY,
    DecisiveReason,
    NotObtained,
)
from estimating.quantities import QuantityItem
from estimating.rules import EstimateLineSpec, MappingRule, RuleSet

MappingStatus = Literal["unique", "ambiguous", "unmapped"]


@dataclass(frozen=True)
class MappedLine:
    """見積の行 1 つと、そこに当てはめた数量。**金額は無い。**"""

    work_item: str
    unit: str
    value_range: tuple[float, float]
    """**行が宣言した単位での**値。正規形から厳密に戻したもの。"""

    canonical_range: tuple[int, int]
    """正規形単位の整数。確定している数量では仲裁層が確定した値。"""

    code: str | None = None
    major_category: str | None = None
    note: str | None = None
    is_confirmed_quantity: bool = False
    """この行の数量を仲裁層が確定させたか。**行が確定したかとは別。**"""

    basis: str = ""
    """この行が何に基づくか(原則5の4つ)。**行を見ただけで分かるように持つ。**

    行が複数の数量から来るようになったら、**いちばん弱いもの**になる
    (`estimating/basis.weakest`)。
    """

    premise_ids: tuple[str, ...] = ()
    """この行が寄りかかっている案件の前提。"""

    hypothesis_premise_ids: tuple[str, ...] = ()
    """そのうち仮説として置かれたもの。**仮説が変わったら見直す行を引くための鍵。**"""

    source_target: str = ""
    """どの数量から来たか。"""

    # -- **何が効いたか。行 1 つを見ただけで分かるように持つ。** ----------------
    # これまでは木の形(`MappingResult` → `QuantityMapping` → `RuleOutcome`)
    # でしか無く、**行だけを取り出す経路**(`settled_lines()`・`as_dict()`)を
    # 通ると落ちていた。報告や見積に出るのはその形である。

    rule_id: str = ""
    """この行を作った規則。"""

    method_id: str = ""
    """元の数量を読んだ手法。"""

    axis_id: str = ""
    """元の数量が出てきた軸。"""

    tier: int | None = None
    """仲裁層の階層。仲裁にかけていなければ None。"""

    action: str | None = None
    """仲裁層の処置(`auto_confirm` / `requires_review` など)。"""

    decisive: tuple[DecisiveReason, ...] = ()
    """**この行が出た決め手**(`estimating/decisive.py` の 5 種類)。

    元の数量の決め手をそのまま運ぶ。**この層で作り直さない。**
    **空は「決め手が無い」**であって「観測だけで出た」ではない。
    """

    def line_key(self) -> tuple[str | None, str, str]:
        """同じ見積の行かどうかを見るための鍵。"""
        return (self.code, self.work_item, self.unit)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "work_item": self.work_item,
            "major_category": self.major_category,
            "unit": self.unit,
            "value_range": list(self.value_range),
            "canonical_range": list(self.canonical_range),
            "is_confirmed_quantity": self.is_confirmed_quantity,
            "basis": self.basis,
            "premise_ids": list(self.premise_ids),
            "hypothesis_premise_ids": list(self.hypothesis_premise_ids),
            "source_target": self.source_target,
            "rule_id": self.rule_id,
            "method_id": self.method_id,
            "axis_id": self.axis_id,
            "tier": self.tier,
            "action": self.action,
            "decisive": [reason.as_dict() for reason in self.decisive],
            "note": self.note,
        }


@dataclass(frozen=True)
class RuleOutcome:
    """1 つの規則が当たった結果。**行の束**であって、行の並びではない。

    取付費と材料費のように 1 つの規則が複数の行を作るとき、その 2 行は
    セットで選ばれる。候補が複数あるときに選ぶのは、行ではなくこの束。
    """

    rule_id: str
    lines: tuple[MappedLine, ...]


@dataclass(frozen=True)
class QuantityMapping:
    """数量 1 件の当てはめ結果。"""

    quantity: QuantityItem
    status: MappingStatus
    outcomes: tuple[RuleOutcome, ...] = ()
    reasons: tuple[str, ...] = ()
    line_collision: bool = False
    """この数量が作る行に、ほかの数量もぶつかっているか。"""

    @property
    def settled(self) -> bool:
        """**確定したか。** 3 つすべてが揃ったときだけ真。"""
        return (
            self.status == "unique"
            and self.quantity.is_confirmed
            and not self.line_collision
        )

    @property
    def lines(self) -> tuple[MappedLine, ...]:
        return tuple(line for outcome in self.outcomes for line in outcome.lines)


@dataclass(frozen=True)
class LineCollision:
    """同じ見積の行に、複数の数量が当たった。**足さずに人へ回す。**"""

    line_key: tuple[str | None, str, str]
    targets: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class MappingResult:
    """当てはめ全体の結果。"""

    ruleset_id: str
    mappings: tuple[QuantityMapping, ...]
    collisions: tuple[LineCollision, ...] = ()

    def settled_lines(self) -> tuple[MappedLine, ...]:
        """確定した見積の行。**2026-09-22 時点では常に空になる。**"""
        return tuple(
            line for mapping in self.mappings if mapping.settled for line in mapping.lines
        )

    def candidates(self) -> tuple[QuantityMapping, ...]:
        """当てはめが一意に決まらなかった数量。"""
        return tuple(m for m in self.mappings if m.status == "ambiguous")

    def unmapped(self) -> tuple[QuantityMapping, ...]:
        """規則が 1 つも当たらなかった数量。**捨てていない。**"""
        return tuple(m for m in self.mappings if m.status == "unmapped")

    def not_obtained(self) -> tuple[NotObtained, ...]:
        """**行にならなかったものを、理由別に数えられる形で返す。**

        おーちゃんの指示(66周目): 取れなかった行も理由別に集計する。
        **この層から分かる理由だけを作る。** 図面が読めなかった側の理由
        (記号が読めない・面積が出せない)は入口しか知らないので、
        `estimating/from_intake.not_obtained_from_intake()` が作る。

        **「その他」には必ず説明を付ける**(束ねて中身を消さない)。
        """
        out: list[NotObtained] = []
        for mapping in self.mappings:
            quantity = mapping.quantity
            if quantity.site_survey_reason:
                out.append(
                    NotObtained(
                        reason=NOT_OBTAINED_SITE_SURVEY,
                        target=quantity.target,
                        detail=quantity.site_survey_reason,
                    )
                )
                continue
            if mapping.status == "unmapped":
                out.append(
                    NotObtained(
                        reason=NOT_OBTAINED_NO_RULE,
                        target=quantity.target,
                        detail="、".join(mapping.reasons),
                    )
                )
            elif mapping.status == "ambiguous":
                out.append(
                    NotObtained(
                        reason=NOT_OBTAINED_OTHER,
                        target=quantity.target,
                        detail="当てはめが一意に決まらない(候補のまま)",
                    )
                )
            elif mapping.line_collision:
                out.append(
                    NotObtained(
                        reason=NOT_OBTAINED_OTHER,
                        target=quantity.target,
                        detail="同じ見積の行に別の数量もぶつかっている(足さずに人へ回す)",
                    )
                )
        return tuple(out)

    def basis_counts_text(self) -> str:
        """行の基づきの内訳。**報告にそのまま貼れる形にする。**"""
        counts: dict[str, int] = {}
        for mapping in self.mappings:
            for line in mapping.lines:
                counts[line.basis] = counts.get(line.basis, 0) + 1
        return "、".join(
            f"{name} {counts[name]} 件"
            for name in BASIS_STRONGEST_FIRST
            if name in counts
        )

    def lines_depending_on(self, premise_id: str) -> tuple[MappedLine, ...]:
        """その前提に寄りかかっている行。

        原則10(おーちゃんの回答): **前提を差し替えたときは、その前提に
        依存している行だけを作り直す。** その「どれか」を引くのがこれである。
        """
        return tuple(
            line
            for mapping in self.mappings
            for line in mapping.lines
            if premise_id in line.premise_ids
        )

    def summary(self) -> str:
        """人が読む要約。報告にそのまま貼れる形にする。"""
        lines = [
            f"規則: {self.ruleset_id}",
            f"数量: {len(self.mappings)} 件",
            f"確定: {len(self.settled_lines())} 件",
            f"候補どまり(当てはめが一意に決まらない): {len(self.candidates())} 件",
            f"当てはまらなかった数量: {len(self.unmapped())} 件",
            "何に基づくか: " + (self.basis_counts_text() or "(行なし)"),
            f"同じ行がぶつかった: {len(self.collisions)} 件(**足していない**)",
        ]
        for mapping in self.mappings:
            head = f"  - {mapping.quantity.target}: {mapping.status}"
            if mapping.line_collision:
                head += " / 行が衝突"
            lines.append(head)
            for outcome in mapping.outcomes:
                for line in outcome.lines:
                    lines.append(
                        f"      [{outcome.rule_id}] {line.work_item}"
                        f" {line.value_range[0]}〜{line.value_range[1]}{line.unit}"
                        f" / {line.basis}"
                    )
                    if line.hypothesis_premise_ids:
                        lines.append(
                            "        依存している仮説: "
                            + "、".join(line.hypothesis_premise_ids)
                        )
            for reason in mapping.reasons:
                lines.append(f"      理由: {reason}")
        for collision in self.collisions:
            lines.append(f"  - 衝突: {collision.detail}")
        return "\n".join(lines)


def map_quantities(
    quantities: Iterable[QuantityItem], ruleset: RuleSet
) -> MappingResult:
    """数量の並びに規則を当てはめる。"""
    drafts: list[QuantityMapping] = []
    for quantity in quantities:
        drafts.append(_map_one(quantity, ruleset))

    collisions = _detect_collisions(drafts)
    colliding_targets = {
        target for collision in collisions for target in collision.targets
    }
    mappings = tuple(
        QuantityMapping(
            quantity=draft.quantity,
            status=draft.status,
            outcomes=draft.outcomes,
            reasons=draft.reasons
            + (
                ("同じ見積の行にほかの数量も当たっているため確定しない(足していない)",)
                if draft.quantity.target in colliding_targets
                else ()
            ),
            line_collision=draft.quantity.target in colliding_targets,
        )
        for draft in drafts
    )
    return MappingResult(
        ruleset_id=ruleset.ruleset_id, mappings=mappings, collisions=collisions
    )


def _map_one(quantity: QuantityItem, ruleset: RuleSet) -> QuantityMapping:
    outcomes: list[RuleOutcome] = []
    reasons: list[str] = []
    for rule in ruleset.rules:
        match = rule.match(quantity)
        if not match.matched:
            # 種類そのものが違う規則の理由は数が多いだけなので残さない。
            # 種類は合っていたのに当たらなかった理由だけを残す。
            if rule.kind == quantity.kind:
                reasons.extend(match.reasons)
            continue
        outcomes.append(
            RuleOutcome(
                rule_id=rule.rule_id, lines=_lines_for(quantity, rule)
            )
        )

    if not outcomes:
        if not reasons:
            reasons.append(
                f"種類 {quantity.kind} に当たる規則が規則ファイルに無い"
            )
        return QuantityMapping(
            quantity=quantity, status="unmapped", reasons=tuple(reasons)
        )

    if len(outcomes) > 1:
        reasons.append(
            "当てはめが一意に決まらない(当たった規則: "
            + "、".join(outcome.rule_id for outcome in outcomes)
            + ")。候補として出し、確定しない"
        )
        return QuantityMapping(
            quantity=quantity,
            status="ambiguous",
            outcomes=tuple(outcomes),
            reasons=tuple(reasons),
        )

    if not quantity.is_confirmed:
        reasons.append(
            f"当てはめは一意だが、数量そのものが確定していない"
            f"(階層{quantity.tier} / {quantity.action})ため確定しない"
        )
    return QuantityMapping(
        quantity=quantity,
        status="unique",
        outcomes=tuple(outcomes),
        reasons=tuple(reasons),
    )


def _lines_for(quantity: QuantityItem, rule: MappingRule) -> tuple[MappedLine, ...]:
    """規則の行の雛形に、数量を入れる。"""
    # 確定している数量では、読んだ値ではなく**仲裁層が確定した値**を使う。
    if quantity.is_confirmed:
        assert quantity.confirmed_range is not None  # is_confirmed が保証する
        canonical = quantity.confirmed_range
    else:
        canonical = quantity.canonical_range

    out: list[MappedLine] = []
    for spec in rule.line_items:
        out.append(
            MappedLine(
                work_item=spec.work_item,
                unit=spec.unit,
                value_range=_in_unit(canonical, spec.unit),
                canonical_range=canonical,
                code=spec.code,
                major_category=spec.major_category,
                note=spec.note,
                is_confirmed_quantity=quantity.is_confirmed,
                # 行が 1 つの数量から来ている今は、その数量の基づきがそのまま
                # 行の基づきになる。**複数から来るようになったら弱いほうを採る。**
                basis=weakest([quantity.basis]),
                premise_ids=tuple(quantity.premise_ids),
                hypothesis_premise_ids=tuple(quantity.hypothesis_premise_ids),
                source_target=quantity.target,
                rule_id=rule.rule_id,
                method_id=quantity.method_id,
                axis_id=quantity.axis_id,
                tier=quantity.tier,
                action=quantity.action,
                decisive=tuple(quantity.decisive),
            )
        )
    return tuple(out)


def _in_unit(canonical: tuple[int, int], unit: str) -> tuple[float, float]:
    """正規形の整数を、行が宣言した単位での値に戻す。

    倍率は 10 のべき乗の文字列なので `Decimal` で厳密に割れる。`float` で
    割ると 955400/10000 が 95.53999999... になりうる。
    """
    factor = Decimal(UNIT_ALIASES[unit][1])
    return (
        float(Decimal(canonical[0]) / factor),
        float(Decimal(canonical[1]) / factor),
    )


def _detect_collisions(mappings: Sequence[QuantityMapping]) -> tuple[LineCollision, ...]:
    """同じ見積の行に複数の数量が当たっていないかを見る。**足さない。**"""
    by_key: dict[tuple[str | None, str, str], list[str]] = {}
    for mapping in mappings:
        seen_here: set[tuple[str | None, str, str]] = set()
        for line in mapping.lines:
            key = line.line_key()
            if key in seen_here:
                continue
            seen_here.add(key)
            by_key.setdefault(key, []).append(mapping.quantity.target)

    out: list[LineCollision] = []
    for key, targets in by_key.items():
        unique_targets = tuple(dict.fromkeys(targets))
        if len(unique_targets) < 2:
            continue
        out.append(
            LineCollision(
                line_key=key,
                targets=unique_targets,
                detail=(
                    f"見積の行「{key[1]}」({key[2]})に "
                    f"{len(unique_targets)} 件の数量が当たった: "
                    + "、".join(unique_targets)
                    + "。**足していない。**どれをこの行の数量にするかは人が決める"
                ),
            )
        )
    return tuple(out)
