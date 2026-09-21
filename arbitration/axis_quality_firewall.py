"""v8 の軸品質・独立性ルールを整合性ソルバーの手前で強制する層。"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, Mapping, Sequence

from arbitration.consistency_solver import ConsistencySolver, IntRange, SolveResult

EvidenceStatus = Literal["confident", "low_confidence", "abstained"]
Strength = Literal["strong", "weak"]

#: レンジが何を数えているか。異なる単位のレンジを重ね合わせてはならない
#: (v8 3-2節)。"count"=個数、"m"=長さ、"m2"=面積、"yen"=金額。
#: 2026-09-21 に追加。それまでは単位の情報がどこにも無く、金額のレンジ
#: (3〜246万円)が個数(建具4本)を「支持」して階層2へ昇格する経路が実在した
#: (`docs/top_priority_unit_safety_defect.md`)。
Unit = str

#: レンジが何を1単位として数えているか。"element"=対象要素1つ、
#: "job"=工事1件。粒度が違うレンジも重ね合わせてはならない。
Granularity = Literal["element", "job"]


@dataclass(frozen=True)
class CenterTolerance:
    """独立したデータ源の**中心値**がどれだけ離れてよいか。

    ``absolute`` は単位そのままの許容差、``relative`` は中心値の平均に対する
    割合。どちらか一方を指定する。比較を厳密な整数演算で行うため
    ``relative`` は :class:`fractions.Fraction` で持つ(float だと
    ``0.05`` が正確に表せず、境界の値の判定が入力によって揺れる)。
    """

    absolute: int | None = None
    relative: Fraction | None = None

    def __post_init__(self) -> None:
        if (self.absolute is None) == (self.relative is None):
            raise ValueError("absolute と relative はどちらか一方だけを指定してください")
        if self.absolute is not None and self.absolute < 0:
            raise ValueError("absolute は0以上にしてください")
        if self.relative is not None and self.relative < 0:
            raise ValueError("relative は0以上にしてください")


#: 単位ごとの中心値の許容差(v8 10章19項の「重なる誤り」対応。2026-09-21)。
#:
#: **個数の ±1 はおーちゃんの判断で確定した値である。** 正しい読みの幅は2で
#: 中心が正解に一致し、正解のレンジと重なる誤りの中心は正解から1.5ずれるので、
#: ±1 はその間に入る。実測でも ±0 にすると正当な一致を取りこぼして精度が
#: 下がった(93.5% → 93.2%。`docs/simulation_tier1_center_agreement.md` 2-4節)。
#:
#: **長さ・面積・金額の 5% は暫定値である。** 絶対値の許容差は連続量では
#: 意味を持たないため相対値にしてあるが、種類ごとの妥当な値は
#: v8 10章12項「許容誤差」の判断と一緒に決める必要がある。
CENTER_TOLERANCES: dict[str, CenterTolerance] = {
    "count": CenterTolerance(absolute=1),
    "mm": CenterTolerance(relative=Fraction(1, 20)),
    "cm2": CenterTolerance(relative=Fraction(1, 20)),
    "yen": CenterTolerance(relative=Fraction(1, 20)),
}

#: 表に無い単位に当てる許容差。**検査を飛ばさない**ための保険である。
#: 飛ばすと「検査はあるのに未知の単位では働かない」状態になり、それは
#: v8 8章の穴2そのもの(集計表を見ても気づけない誤り)になる。
#: 使われたことは判定理由に必ず残す。
UNKNOWN_UNIT_CENTER_TOLERANCE = CenterTolerance(relative=Fraction(1, 20))


def _format_center(doubled: int) -> str:
    """2倍で持っている中心値を、人が読める形に戻す(桁区切りを入れる)。"""
    whole, remainder = divmod(doubled, 2)
    return f"{whole:,}.5" if remainder else f"{whole:,}"


@dataclass(frozen=True)
class AxisEvidence:
    """1手法の読み取りと、その独立性・品質メタデータ。"""

    target: str
    count_range: IntRange
    source_id: str
    axis_id: str
    method_id: str
    #: 必須。既定値を与えると、単位を意識せずに書かれた呼び出しが黙って
    #: 通ってしまい、迂回経路が残るため。
    unit: Unit
    granularity: Granularity = "element"
    source_fingerprint: str | None = None
    strength: Strength = "strong"
    status: EvidenceStatus = "confident"
    calibrated: bool = True
    model_confidence: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lower, upper = self.count_range
        if lower > upper:
            raise ValueError(f"count_range の下限({lower})が上限({upper})を超えています")
        if not self.source_id or not self.axis_id or not self.method_id:
            raise ValueError("source_id / axis_id / method_id は空にできません")
        if not self.unit:
            raise ValueError("unit は空にできません(レンジが何を数えているかを必ず宣言する)")
        if self.granularity not in ("element", "job"):
            raise ValueError(f"granularity は element / job のいずれか: {self.granularity}")

    @property
    def evidence_id(self) -> str:
        return f"{self.source_id}::{self.axis_id}::{self.method_id}::{self.target}"

    @property
    def independence_key(self) -> str:
        """独立性は表示用IDではなく、可能なら元データの不変な指紋で判定する。"""
        return self.source_fingerprint or self.source_id

    @property
    def is_hard_eligible(self) -> bool:
        """内部confidenceではなく、強度・状態・実測校正の全条件で判定する。"""
        return (
            self.strength == "strong"
            and self.status == "confident"
            and self.calibrated
        )


@dataclass(frozen=True)
class EscalationRequest:
    failure_type: str
    conflicting_constraints: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class FirewallDecision:
    tier: int
    action: Literal["auto_confirm", "provisional_audit", "requires_review"]
    confirmed_range: IntRange | None
    independent_strong_source_count: int
    independent_advisory_source_count: int
    method_count: int
    reasons: tuple[str, ...]
    solve_result: SolveResult
    hard_evidence_ids: tuple[str, ...]
    advisory_evidence_ids: tuple[str, ...]
    abstained_evidence_ids: tuple[str, ...]
    escalation: EscalationRequest | None = None


class AxisQualityFirewall:
    """相関した手法と未校正情報を、Z3のハード制約から分離する。"""

    def __init__(
        self, *, center_tolerances: Mapping[str, CenterTolerance] | None = None
    ) -> None:
        """``center_tolerances`` で単位ごとの中心値の許容差を差し替えられる。

        省略すると :data:`CENTER_TOLERANCES` を使う。テストと、種類ごとの
        許容差を運用側で決める場合のための入口である。
        """
        self._center_tolerances: dict[str, CenterTolerance] = dict(
            CENTER_TOLERANCES if center_tolerances is None else center_tolerances
        )

    # ------------------------------------------------------------------
    # 中心値の一致(v8 10章19節の「重なる誤り」対応)
    # ------------------------------------------------------------------

    def _tolerance_for(self, unit: Unit) -> tuple[CenterTolerance, bool]:
        """単位に当てる許容差と、表に無くて保険を使ったかどうか。"""
        found = self._center_tolerances.get(unit)
        if found is not None:
            return found, False
        return UNKNOWN_UNIT_CENTER_TOLERANCE, True

    def _centers_disagree(
        self, ranges: Sequence[IntRange], unit: Unit
    ) -> tuple[bool, str | None]:
        """独立したデータ源の中心値が許容差を超えて離れているか。

        **レンジが重なっていても、中心が離れていれば確定してはならない。**
        重なる誤りは積集合が空にならないので矛盾として検出できず、そのまま
        確定すると誤った値がクラスタの等式で伝播する(実測では30試行のうち
        25試行で、階層を入れたほうが階層なしより精度が低くなった。
        `docs/simulation_tier1_center_agreement.md`)。

        中心値は ``(下限+上限)/2`` だが、**2倍したまま整数で比較する。**
        0.5 を float で扱うと境界の判定が入力によって揺れるため。
        戻り値の2つ目は、離れていた場合の理由の文言(離れていなければ None)。
        """
        if len(ranges) < 2:
            return False, None
        doubled = [low + high for low, high in ranges]
        spread = max(doubled) - min(doubled)
        tolerance, used_fallback = self._tolerance_for(unit)
        note = f"(単位 {unit} が許容差の表に無いため既定の相対許容差を適用)" if used_fallback else ""

        if tolerance.absolute is not None:
            over = spread > 2 * tolerance.absolute
            limit = f"±{tolerance.absolute}({unit})"
        else:
            assert tolerance.relative is not None  # __post_init__ が保証する
            # spread/2 > relative * mean(doubled/2) を、分母を払って整数で比較する。
            over = spread * len(doubled) > tolerance.relative * sum(doubled)
            limit = f"中心値の平均の ±{float(tolerance.relative) * 100:.3g}%"
        if not over:
            return False, None
        centers = ", ".join(_format_center(d) for d in sorted(doubled))
        return True, (
            f"独立したデータ源の中心値が許容差({limit})を超えて離れているため"
            f"自動で確定しない{note}。中心値: {centers}"
        )

    @staticmethod
    def _union_ranges_by_source(
        evidences: Sequence[AxisEvidence],
    ) -> list[IntRange]:
        """棄権していない証拠を、データ源ごとに**和集合**でまとめる。

        階層2の検査に使う。階層2は「強い軸1つ + それを支持する弱い軸2つ以上」で
        成立するので、強い軸同士だけを見る検査では**独立した強い軸が1つしか
        無いこの形を検出できない**(実測: 劣化した条件の95要素すべてが強い軸1つ
        で、階層1の要素は0件だった)。弱い軸まで含めて中心値を見る必要がある。
        """
        by_source: dict[str, list[AxisEvidence]] = {}
        for item in evidences:
            if item.status == "abstained":
                continue
            by_source.setdefault(item.independence_key, []).append(item)
        return [
            (
                min(i.count_range[0] for i in items),
                max(i.count_range[1] for i in items),
            )
            for items in by_source.values()
        ]

    def assess(self, evidences: Sequence[AxisEvidence]) -> FirewallDecision:
        if not evidences:
            return self._empty_decision(method_count=0)

        targets = {item.target for item in evidences}
        if len(targets) != 1:
            raise ValueError("1回の判定では同一targetの証拠だけを渡してください")
        target = evidences[0].target
        reasons: list[str] = []

        # v8 3-2節: 単位・粒度が一致していない証拠は重ね合わせてはならない。
        # レンジが数値として重なることは、支持の証拠にならない。黙って落とさず
        # エスカレーションする(取り違えは人が直すべき入力の誤りなので)。
        mismatch = self._unit_mismatch(evidences)
        if mismatch is not None:
            return self._unit_mismatch_decision(evidences, mismatch)

        abstained = [item for item in evidences if item.status == "abstained"]
        hard_candidates = [item for item in evidences if item.is_hard_eligible]
        advisory = [
            item
            for item in evidences
            if item.status != "abstained" and not item.is_hard_eligible
        ]

        for item in advisory:
            if not item.calibrated:
                reasons.append(
                    f"{item.method_id} は実測校正を通過していないため、内部confidenceに関係なく参考情報"
                )
            elif item.strength == "weak" or item.status == "low_confidence":
                reasons.append(f"{item.method_id} は弱い軸のためハード制約に不参加")

        by_source: dict[str, list[AxisEvidence]] = {}
        for item in evidences:
            by_source.setdefault(item.independence_key, []).append(item)
        for source_id, items in by_source.items():
            if len({item.method_id for item in items}) > 1:
                reasons.append(
                    f"同一データ源 {source_id} の複数手法は、独立軸として重複加算しない"
                )

        hard_by_source: dict[str, list[AxisEvidence]] = {}
        for item in hard_candidates:
            hard_by_source.setdefault(item.independence_key, []).append(item)

        # 同じsourceのレンズ同士が食い違う場合、そのsource全体をハードから降格する。
        usable_sources: dict[str, tuple[IntRange, list[AxisEvidence]]] = {}
        for source_id, items in hard_by_source.items():
            lower = max(item.count_range[0] for item in items)
            upper = min(item.count_range[1] for item in items)
            if lower <= upper:
                usable_sources[source_id] = ((lower, upper), items)
            else:
                advisory.extend(items)
                reasons.append(
                    f"同一データ源 {source_id} 内で手法が矛盾したため、そのsourceをハード制約から降格"
                )

        if len(usable_sources) < 2 and any(
            len({item.method_id for item in items}) > 1 for items in by_source.values()
        ):
            reasons.append("同一データ源による相関誤りの可能性があるため、自動確定しない")

        solver = ConsistencySolver()
        if usable_sources:
            all_ranges = [value[0] for value in usable_sources.values()]
            solver.add_variable(
                target,
                min(item[0] for item in all_ranges),
                max(item[1] for item in all_ranges),
                axis="quality-firewall",
            )
            for index, (source_id, (source_range, items)) in enumerate(
                sorted(usable_sources.items())
            ):
                source_var = f"{target}__source_{index}"
                solver.add_variable(
                    source_var,
                    source_range[0],
                    source_range[1],
                    axis=items[0].axis_id,
                    evidence={"source_id": source_id},
                )
                solver.add_relation(
                    f"source_link::{source_id}",
                    target,
                    "==",
                    source_var,
                    description=f"{source_id} と統合対象の一致",
                )

        solve_result = solver.solve()
        usable_items = [item for _, items in usable_sources.values() for item in items]
        hard_ids = tuple(item.evidence_id for item in usable_items)
        advisory_ids = tuple(dict.fromkeys(item.evidence_id for item in advisory))
        abstained_ids = tuple(item.evidence_id for item in abstained)
        advisory_source_count = len({item.independence_key for item in advisory})

        if solve_result.status == "unsat":
            reasons.append("独立した強い軸同士の制約が矛盾したためエスカレーション")
            return FirewallDecision(
                tier=3,
                action="requires_review",
                confirmed_range=None,
                independent_strong_source_count=len(usable_sources),
                independent_advisory_source_count=advisory_source_count,
                method_count=len(evidences),
                reasons=tuple(dict.fromkeys(reasons)),
                solve_result=solve_result,
                hard_evidence_ids=hard_ids,
                advisory_evidence_ids=advisory_ids,
                abstained_evidence_ids=abstained_ids,
                escalation=EscalationRequest(
                    failure_type="axis_contradiction",
                    conflicting_constraints=solve_result.conflicting_constraints,
                    reason="独立した強い軸の積集合が空です",
                ),
            )

        candidate_range = (
            solve_result.variables[target].solved_range
            if target in solve_result.variables
            else None
        )
        strong_count = len(usable_sources)
        unit = evidences[0].unit
        #: 中心値が離れていて確定できなかった場合の理由(v8 10章19項)。
        center_note: str | None = None
        if strong_count >= 2:
            # **レンジが重なっているだけでは確定の根拠にならない。**
            # 重なる誤りは積集合が空にならないので矛盾として検出できず、
            # そのまま階層1で確定すると人が一度も見ないまま誤った値が通る
            # (実案件の床面積で実在: 文章軸 99.5〜100.5㎡ と VTracer
            #  100.0〜162.5㎡ が「一致」と判定されていた)。
            disagree, center_note = self._centers_disagree(
                [source_range for source_range, _ in usable_sources.values()], unit
            )
            if disagree:
                tier = 3
                action = "requires_review"
                reasons.append(center_note)
            else:
                tier = 1
                action = "auto_confirm"
                reasons.append("実測校正済みの独立した強いデータ源が2つ以上一致")
        elif strong_count == 1 and self._has_two_agreeing_advisory_sources(
            advisory, candidate_range
        ):
            # **階層2にも同じ検査を入れる。** 階層1だけに入れても、劣化が
            # 実測された条件(強い軸1つ+弱い軸2つ)には階層1の要素が1件も
            # 無いため1列も変わらなかった
            # (`docs/simulation_tier1_center_agreement.md` 2-1節)。
            # ここでは弱い軸まで含めて中心値を見る。強い軸が誤った重なる
            # レンジを出しているのに弱い軸が正解側を指している形を捕まえる
            # ため(実測: image=(3,4) 誤り / weak=(4,6) / 正解5 が、
            # クラスタの等式で {4} に潰れて3要素を汚染していた)。
            disagree, center_note = self._centers_disagree(
                self._union_ranges_by_source(evidences), unit
            )
            if disagree:
                tier = 3
                action = "requires_review"
                reasons.append(center_note)
            else:
                tier = 2
                action = "provisional_audit"
                reasons.append("強い独立データ源1つを、異なる弱いデータ源2つ以上が支持")
        else:
            tier = 3
            action = "requires_review"
            reasons.append("自動確定に必要な独立した強いデータ源が2つ未満")

        if action == "requires_review" and center_note is not None:
            # **確定範囲を残さない。** 残すと呼び出し側が「確定済み」と
            # 読み違える経路ができる(バグ②と同根。
            # `docs/top_priority_unit_safety_defect.md`)。
            return FirewallDecision(
                tier=tier,
                action=action,
                confirmed_range=None,
                independent_strong_source_count=strong_count,
                independent_advisory_source_count=advisory_source_count,
                method_count=len(evidences),
                reasons=tuple(dict.fromkeys(reasons)),
                solve_result=solve_result,
                hard_evidence_ids=hard_ids,
                advisory_evidence_ids=advisory_ids,
                abstained_evidence_ids=abstained_ids,
                escalation=EscalationRequest(
                    failure_type="center_disagreement",
                    conflicting_constraints=(),
                    reason=center_note,
                ),
            )

        return FirewallDecision(
            tier=tier,
            action=action,
            confirmed_range=candidate_range,
            independent_strong_source_count=strong_count,
            independent_advisory_source_count=advisory_source_count,
            method_count=len(evidences),
            reasons=tuple(dict.fromkeys(reasons)),
            solve_result=solve_result,
            hard_evidence_ids=hard_ids,
            advisory_evidence_ids=advisory_ids,
            abstained_evidence_ids=abstained_ids,
        )

    @staticmethod
    def _unit_mismatch(evidences: Sequence[AxisEvidence]) -> str | None:
        """単位・粒度が揃っていなければ、その理由を返す(揃っていれば None)。"""
        units = sorted({item.unit for item in evidences})
        if len(units) > 1:
            return f"単位が混在しています: {units}"
        granularities = sorted({item.granularity for item in evidences})
        if len(granularities) > 1:
            return f"粒度が混在しています: {granularities}"
        return None

    def _unit_mismatch_decision(
        self, evidences: Sequence[AxisEvidence], reason: str
    ) -> FirewallDecision:
        solve_result = ConsistencySolver().solve()
        return FirewallDecision(
            tier=3,
            action="requires_review",
            confirmed_range=None,
            independent_strong_source_count=0,
            independent_advisory_source_count=0,
            method_count=len(evidences),
            reasons=(
                f"{reason}。単位・粒度の違うレンジは重ね合わせない(v8 3-2節)",
            ),
            solve_result=solve_result,
            hard_evidence_ids=(),
            advisory_evidence_ids=(),
            abstained_evidence_ids=tuple(item.evidence_id for item in evidences),
            escalation=EscalationRequest(
                failure_type="unit_mismatch",
                conflicting_constraints=(),
                reason=reason,
            ),
        )

    @staticmethod
    def _has_two_agreeing_advisory_sources(
        advisory: Sequence[AxisEvidence], candidate_range: IntRange | None
    ) -> bool:
        if candidate_range is None:
            return False
        lower, upper = candidate_range
        sources = {
            item.independence_key
            for item in advisory
            if not (item.count_range[1] < lower or item.count_range[0] > upper)
        }
        return len(sources) >= 2

    @staticmethod
    def _empty_decision(*, method_count: int) -> FirewallDecision:
        result = ConsistencySolver().solve()
        return FirewallDecision(
            tier=3,
            action="requires_review",
            confirmed_range=None,
            independent_strong_source_count=0,
            independent_advisory_source_count=0,
            method_count=method_count,
            reasons=("判定に利用できる証拠がない",),
            solve_result=result,
            hard_evidence_ids=(),
            advisory_evidence_ids=(),
            abstained_evidence_ids=(),
        )
