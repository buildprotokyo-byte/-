"""図面の入口から見積の行の一覧までを 1 本につなぐ。**金額は出さない。**

**なぜこれが要るのか**

`intake/drawing_intake.read_drawing()`、`estimating/from_intake.py`、
`estimating/mapping.py`、`estimating/standing_lines.py` は全部あったが、
**この 4 つを順につなぐ呼び出しがどこにも無かった**(2026-09-23 に確認。
`map_quantities` を呼ぶ本番の経路は 0 件で、`app.py` は空だった)。

そのため、A-1・A-2 の報告はどれも「見積の行が埋まるかどうかは測っていない」で
終わっていた。**測れなかったのではなく、測る先が無かった。**

ここが作るのは**行の一覧と、足りないものの一覧**だけである。
金額・単価は一切扱わない(単価をコードに持たない、という約束を変えない)。

**何も確定させない**

- 読み取った数量の確定は仲裁層が決める。ここはその結果をそのまま運ぶ。
- 当てはめが一意に決まらない数量は候補のまま出す。
- 図面からは決まらない行は、会社のルールに基づくので**常に人の確認が要る**。

**足りないものを黙らせない**

見積に行が無いとき、理由は 3 つありうる。

1. 工事にその行が無い
2. 図面から読めなかった
3. **会社のルールを持っていない**

いまは 3 つが見分けられない。この層は 2 と 3 を `gaps` に出して分ける。
**1 だと言えるのは、2 と 3 のどちらでもないと示せたときだけである。**
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from estimating.case_premises import PremiseSet, apply_premises
from estimating.from_intake import quantities_from_intake
from estimating.mapping import MappedLine, MappingResult, map_quantities
from estimating.quantities import QuantityItem
from estimating.rules import RuleSet
from estimating.standing_lines import StandingGap, StandingLine, StandingResult, apply_standing_lines


@dataclass(frozen=True)
class EstimateDraft:
    """見積の下書き。**行の一覧と、足りないものの一覧。金額は無い。**"""

    ruleset_id: str
    mapping: MappingResult
    standing: StandingResult
    quantities: tuple[QuantityItem, ...] = field(default_factory=tuple)

    @property
    def settled_lines(self) -> tuple[MappedLine, ...]:
        """確定した行。**手法が 1 つも校正されていない今は常に空。**"""
        return self.mapping.settled_lines()

    @property
    def candidate_lines(self) -> tuple[MappedLine, ...]:
        """候補どまりの行(当てはめが一意に決まらなかった数量から来たもの)。"""
        return tuple(
            line for mapping in self.mapping.candidates() for line in mapping.lines
        )

    @property
    def standing_lines(self) -> tuple[StandingLine, ...]:
        """図面からは決まらない行。**常に人の確認が要る。**"""
        return self.standing.lines

    def gaps(self) -> tuple[str, ...]:
        """**なぜ行が無いのか**を人が読める文で並べる。

        「行が無い」で終わらせないための一覧である。
        """
        out: list[str] = []
        for mapping in self.mapping.unmapped():
            out.append(
                f"[規則が当たらない] {mapping.quantity.target}: "
                "読めた数量に当てはまる規則が無い。**数量は捨てていない**"
            )
        for mapping in self.mapping.candidates():
            out.append(
                f"[当てはめが一意でない] {mapping.quantity.target}: "
                f"規則が {len(mapping.outcomes)} 件当たったので確定させない"
            )
        for gap in self.standing.gaps:
            out.append(f"[{gap.kind}] {gap.message}")
        for collision in self.mapping.collisions:
            out.append(f"[同じ行に複数の数量] {collision}")
        return tuple(out)

    def summary(self) -> str:
        """人が読む要約。**報告にそのまま貼れる形にする。**"""
        return "\n".join(
            [
                self.mapping.summary(),
                self.standing.summary(),
                f"足りないもの: {len(self.gaps())} 件",
                "**金額は出していない。行と数量だけである。**",
            ]
        )


def build_estimate_draft(
    intake_result,
    ruleset: RuleSet,
    premises: PremiseSet | None = None,
) -> EstimateDraft:
    """図面の入口の結果から、見積の下書きを作る。

    引数の型を注釈で縛らないのは、この層が `intake/` に依存しないため
    (`estimating/from_intake.py` と同じ理由)。必要なのは
    `findings` / `decisions` / `door_schedule_rows` だけである。
    """
    quantities = quantities_from_intake(intake_result)
    if premises is not None:
        quantities = apply_premises(quantities, premises)
    return build_estimate_draft_from_quantities(quantities, ruleset)


def build_estimate_draft_from_quantities(
    quantities: Sequence[QuantityItem],
    ruleset: RuleSet,
) -> EstimateDraft:
    """すでに数量になっているものから下書きを作る(試験・検討用)。"""
    mapping = map_quantities(quantities, ruleset)
    standing = apply_standing_lines(ruleset, quantities)
    return EstimateDraft(
        ruleset_id=ruleset.ruleset_id,
        mapping=mapping,
        standing=standing,
        quantities=tuple(quantities),
    )


__all__ = [
    "EstimateDraft",
    "StandingGap",
    "build_estimate_draft",
    "build_estimate_draft_from_quantities",
]
