"""図面からは決まらない見積の行(会社のルールだけで立つ行)。

**なぜ要るのか**

仮設水道料・仮設電気料・小運搬費・荷上費・墨出し・竣工時清掃・駐車場代は、
読み方の比較実験の 6 本の答案すべてで **0 件**だった。図面のどこにも書かれて
いないので、読み取りをどれだけ良くしても 1 行も埋まらない。
これらは**会社のルールがあって初めて立つ行**である。

**この実装が守ること**

1. **ルールが無いときに、黙って 0 行にしない。** いまは
   「その行が無い見積」と「その行のルールを持っていない見積」が
   見分けられない。**見分けられるようにするのがこの実装のいちばんの目的。**
2. **行を捨てない。** もとになる数量が無くても、率が決まっていなくても、
   行だけは出して「何が足りないか」を添える。
3. **数量を勝手に埋めない。** 率が空なら数量は入らない
   (原則: 申告が無い数量を既定値で埋めない)。
4. **自動で確定させない。** 図面ではなく会社のルールに基づく行なので、
   人の確認が要る。

**ルールの中身はここに書かない**

`estimating/rules.py` と同じで、規則は差し替えられる外部ファイルとして読む。
リポジトリに置くのは**架空の見本**だけで、実際のルールはリポジトリの外に置き、
パスを設定で渡す。**率・式・どの工種に何を掛けるかは、おーちゃんの確認が要る。**
この実装は**形と動作まで**である。
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from estimating.quantities import QuantityItem, split_target
from estimating.rules import RuleSet, StandingLineSpec

#: 足りないものの種類。
GAP_NO_STANDING_RULES = "会社のルールが与えられていない"
GAP_MISSING_QUANTITY = "もとになる数量が無い"
GAP_AMBIGUOUS_QUANTITY = "もとになる数量が複数あって選べない"
GAP_EMPTY_BASIS = "基準の中身が決まっていない"

#: これらの行が「何に基づいているか」。`estimating/basis.py` の言葉と揃える。
BASIS_COMPANY_RULE = "会社のルールに基づく"


@dataclass(frozen=True)
class StandingGap:
    """行を作れなかった/数量を入れられなかった理由 1 件。**人が読める文で残す。**"""

    kind: str
    message: str
    standing_id: str | None = None


@dataclass(frozen=True)
class StandingLine:
    """会社のルールだけで立った見積の行 1 件。"""

    standing_id: str
    work_item: str
    major_category: str
    unit: str

    quantity_range: tuple[float, float] | None
    """数量。**入れられなければ None。0 や 1 で埋めない。**"""

    source_targets: tuple[str, ...] = ()
    """数量のもとにした対象名。一式の行では空。"""

    basis: str = BASIS_COMPANY_RULE
    settled: bool = False
    """**常に False。** 図面ではなく会社のルールに基づく行なので確定させない。"""

    requires_human_confirmation: bool = True
    note: str | None = None


@dataclass(frozen=True)
class StandingResult:
    """図面からは決まらない行の当てはめ結果。"""

    ruleset_id: str
    has_standing_rules: bool
    lines: tuple[StandingLine, ...] = ()
    gaps: tuple[StandingGap, ...] = ()

    def summary(self) -> str:
        """人が読む要約。**報告にそのまま貼れる形にする。**"""
        if not self.has_standing_rules:
            return (
                f"規則: {self.ruleset_id} / "
                "図面からは決まらない行の会社のルールが**与えられていない**。"
                "この見積には、図面からは決まらない行が 1 行も入っていない。"
                "**「その行が無い」ではなく「ルールが欠けている」である。**"
            )
        with_quantity = sum(1 for line in self.lines if line.quantity_range is not None)
        parts = [
            f"規則: {self.ruleset_id}",
            f"図面からは決まらない行: {len(self.lines)} 件",
            f"うち数量が入った行: {with_quantity} 件",
            f"足りないもの: {len(self.gaps)} 件",
        ]
        return " / ".join(parts)


def apply_standing_lines(
    ruleset: RuleSet,
    quantities: Sequence[QuantityItem] = (),
) -> StandingResult:
    """会社のルールだけで立つ行を作る。

    **規則が無ければ「ルールが欠けている」を返す。** 黙って空を返さない。
    """
    if not ruleset.standing_lines:
        return StandingResult(
            ruleset_id=ruleset.ruleset_id,
            has_standing_rules=False,
            gaps=(
                StandingGap(
                    kind=GAP_NO_STANDING_RULES,
                    message=(
                        "図面からは決まらない行(仮設・運搬・墨出し・清掃など)の"
                        "会社のルールが与えられていない。"
                        "この見積にそれらの行が入っていないのは、"
                        "**工事にその行が無いからではなく、ルールを持っていないから**である"
                    ),
                ),
            ),
        )

    by_kind: dict[str, list[QuantityItem]] = {}
    for quantity in quantities:
        kind, _ = split_target(quantity.target)
        by_kind.setdefault(kind, []).append(quantity)

    lines: list[StandingLine] = []
    gaps: list[StandingGap] = []
    for spec in ruleset.standing_lines:
        quantity_range, targets, gap = _quantity_for(spec, by_kind)
        if gap is not None:
            gaps.append(gap)
        lines.append(
            StandingLine(
                standing_id=spec.standing_id,
                work_item=spec.work_item,
                major_category=spec.major_category,
                unit=spec.unit,
                quantity_range=quantity_range,
                source_targets=targets,
                note=spec.note,
            )
        )
    return StandingResult(
        ruleset_id=ruleset.ruleset_id,
        has_standing_rules=True,
        lines=tuple(lines),
        gaps=tuple(gaps),
    )


def _quantity_for(
    spec: StandingLineSpec, by_kind: dict[str, list[QuantityItem]]
) -> tuple[tuple[float, float] | None, tuple[str, ...], StandingGap | None]:
    if spec.basis_kind == "一式":
        value = float(spec.lump_sum_quantity)
        return (value, value), (), None

    # 数量参照
    candidates = by_kind.get(spec.target_kind or "", [])
    if not candidates:
        return (
            None,
            (),
            StandingGap(
                kind=GAP_MISSING_QUANTITY,
                message=(
                    f"「{spec.work_item}」は {spec.target_kind} を使う規則だが、"
                    f"{spec.target_kind} が読めていない。"
                    "**行は残し、数量は入れない。**"
                ),
                standing_id=spec.standing_id,
            ),
        )
    if len(candidates) > 1:
        names = "・".join(sorted(item.target for item in candidates))
        return (
            None,
            (),
            StandingGap(
                kind=GAP_AMBIGUOUS_QUANTITY,
                message=(
                    f"「{spec.work_item}」のもとになる {spec.target_kind} が"
                    f"複数ある({names})。**どれかを選ばない。**"
                ),
                standing_id=spec.standing_id,
            ),
        )
    if spec.per_unit is None:
        return (
            None,
            (),
            StandingGap(
                kind=GAP_EMPTY_BASIS,
                message=(
                    f"「{spec.work_item}」の 1 単位あたりの値が規則に書かれていない。"
                    "**行は残し、数量は入れない**(勝手に 1 で埋めない)"
                ),
                standing_id=spec.standing_id,
            ),
        )
    quantity = candidates[0]
    lower, upper = quantity.value_range
    return (
        (lower * spec.per_unit, upper * spec.per_unit),
        (quantity.target,),
        None,
    )
