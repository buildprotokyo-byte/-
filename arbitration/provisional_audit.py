"""階層2(仮採用+抜き取り監査)の、抜き取り監査そのもの。

位置づけ(`docs/design_v8.md` 3-3節・4-3節)
--------------------------------------------
階層2は**自動採用される**階層である。その安全性は、v8 3-3節の
「自動採用するが、**一定割合を事後監査する**」という前提にだけ支えられている。

**2026-09-21 の全数点検まで、この監査は実装されていなかった**
(`docs/audit_unimplemented_trial_claims.md` 3節)。コードにあったのは階層2と
いう名前(`firewall_provisional`)と、精密モードで監査を迂回する分岐だけで、
監査の割合・対象選定・母集団を決めるものは1行も無かった。**監査が無いまま
階層2を自動採用することは、実質的に無検査の自動確定である。** このモジュールは
その欠落を埋める。

この監査が「存在するが機能していない」状態に陥る3つの道
--------------------------------------------------------
実装にあたって最も警戒したのは、**監査が形式的に走っているのに何も検出して
いない**状態である。次の3つは、いずれも「緑のまま素通りする」ため、
集計表を見ても気づけない。v8 8章の穴2そのものなので、**型と例外で塞いでいる。**

1. **正解が無いのに的中率を出す。** 実運用の監査時点では正解は存在しない
   (だから人が確認する)。正解が無い対象を「一致」と数えると、的中率は
   常に100%になる。**``hit_rate`` は照合できた対象が1件も無ければ
   ``None`` を返し、1.0 を返さない。**
2. **母集団が空なのに「監査済み」と報告する。** 階層2の要素が0件の状態と、
   監査して全部当たった状態は、まったく違う。``AuditReport.status`` で
   区別する。
3. **抽出率を掛けた結果が0件になる。** 母集団が少ないと
   ``int(3 * 0.3) == 0`` で、監査が静かに空回りする。**最小抽出件数を
   設けて、母集団が1件でもあれば必ず1件以上抽出する。**

検出力について正直に言っておくこと
------------------------------------
抜き取り監査は**誤りの存在を確率的にしか検出できない。** 母集団 N 件のうち
誤りが k 件あるとき、n 件を非復元抽出して1件以上捕まえる確率は
超幾何分布で決まる(``detection_probability()`` で実際に計算できる)。

たとえば母集団10件・誤り1件のとき、抽出率30%(3件)では **30%** しか捕まえ
られない。5件に増やすと50%になる。**「監査しているから安全」とは言えず、
「この監査はこれだけの確率で見逃す」と言うのが正しい。** 報告にもその数値を
必ず載せる。

抽出率の決め方
----------------
既定は **30%、ただし最小5件**(母集団がそれより小さければ全件)。
30% はおーちゃんの提案値で、そのまま採用した。最小5件を足したのは上記3の
空回りを防ぐためで、母集団10件・誤り1件のときの検出確率が 30%→50% に
上がることを実測して決めた(`benchmarks/run_provisional_audit_eval.py`)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import comb
from typing import Callable, Literal, Mapping, Sequence

IntRange = tuple[int, int]

#: 既定の抽出率。おーちゃんの提案値(2026-09-21)。
DEFAULT_SAMPLING_RATE = 0.30

#: 既定の最小抽出件数。母集団がこれより小さければ全件を監査する。
#: 抽出率だけだと母集団が少ないときに0件になり、監査が静かに空回りする。
DEFAULT_MINIMUM_SAMPLE = 5

#: 監査の結論。
#: - match:        採用した範囲に正解が入っていた
#: - mismatch:     採用した範囲の外に正解があった(階層2の自動採用が誤っていた)
#: - unverifiable: 正解が無いので判定できない。**人の確認待ちであって、一致ではない**
AuditOutcome = Literal["match", "mismatch", "unverifiable"]

#: 監査全体の状態。
#: - no_population:      階層2の要素が1件も無い(監査する対象が無い)
#: - verified:           照合できた対象があり、的中率を出せた
#: - awaiting_human:     抽出はしたが、正解が無く1件も照合できていない
AuditStatus = Literal["no_population", "verified", "awaiting_human"]


class AuditConfigurationError(Exception):
    """監査が空回りする設定を、実行前に止めるための例外。"""


@dataclass(frozen=True)
class AuditCandidate:
    """監査の母集団に入る、階層2の要素1件。"""

    target: str
    #: 階層2として自動採用された範囲。
    adopted_range: IntRange
    unit: str
    #: v8 4-3節ルール3の「同じカテゴリ」。誤りが見つかったときに
    #: 監査を広げる単位で、既定では手法 ID を使う(系統誤差は手法から出るため)。
    #: 何をカテゴリとするかは設計判断なので、呼び出し側が明示できるようにしている。
    category: str
    axis_id: str = ""
    method_id: str = ""

    def contains(self, value: int) -> bool:
        return self.adopted_range[0] <= value <= self.adopted_range[1]


@dataclass(frozen=True)
class AuditFinding:
    """抽出した1件の監査結果。"""

    target: str
    outcome: AuditOutcome
    adopted_range: IntRange
    ground_truth: int | None
    category: str
    note: str = ""

    @property
    def is_verifiable(self) -> bool:
        return self.outcome != "unverifiable"


@dataclass(frozen=True)
class AuditLogEntry:
    """監査が実行されたことを示す記録。

    **監査が走ったかどうかを、あとから確認できるようにするためのもの。**
    「監査しているつもりで何も走っていない」状態を検出できるよう、
    母集団・抽出件数・乱数シードをすべて残す。
    """

    recorded_at: datetime
    population_size: int
    sample_size: int
    sampling_rate: float
    minimum_sample: int
    seed: int
    sampled_targets: tuple[str, ...]
    status: AuditStatus

    def format_line(self) -> str:
        stamp = self.recorded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"[{stamp}] provisional_audit "
            f"population={self.population_size} sample={self.sample_size} "
            f"rate={self.sampling_rate:.2f} min={self.minimum_sample} "
            f"seed={self.seed} status={self.status} "
            f"targets={','.join(self.sampled_targets) or '-'}"
        )


@dataclass(frozen=True)
class AuditReport:
    """抜き取り監査1回分の結果。"""

    status: AuditStatus
    population_size: int
    sample_size: int
    sampling_rate: float
    minimum_sample: int
    seed: int
    findings: tuple[AuditFinding, ...]
    log: AuditLogEntry
    #: v8 4-3節ルール3で、監査を広げるべきカテゴリ(誤りが見つかったもの)。
    expanded_categories: tuple[str, ...] = ()

    @property
    def verifiable_count(self) -> int:
        return sum(1 for f in self.findings if f.is_verifiable)

    @property
    def match_count(self) -> int:
        return sum(1 for f in self.findings if f.outcome == "match")

    @property
    def mismatch_count(self) -> int:
        return sum(1 for f in self.findings if f.outcome == "mismatch")

    @property
    def unverifiable_count(self) -> int:
        return sum(1 for f in self.findings if f.outcome == "unverifiable")

    @property
    def hit_rate(self) -> float | None:
        """的中率。**照合できた対象が1件も無ければ ``None``。**

        ここで 1.0 を返してしまうと、正解が1件も無い状態が「全部当たった」
        と report され、監査が空回りしていることに誰も気づけなくなる。
        このモジュールの docstring の1番目の穴。
        """
        if self.verifiable_count == 0:
            return None
        return self.match_count / self.verifiable_count

    @property
    def found_error(self) -> bool:
        return self.mismatch_count > 0

    def detection_probability(self, assumed_error_count: int) -> float:
        """誤りが ``assumed_error_count`` 件あるとき、1件以上捕まえる確率。

        超幾何分布。**「監査しているから安全」ではなく「この監査はこれだけの
        確率で見逃す」を数値で言うためのもの。**
        """
        n_pop, n_sample, k = self.population_size, self.sample_size, assumed_error_count
        if k <= 0 or n_pop <= 0 or n_sample <= 0:
            return 0.0
        k = min(k, n_pop)
        if n_sample >= n_pop:
            return 1.0
        if n_pop - k < n_sample:
            return 1.0
        return 1.0 - comb(n_pop - k, n_sample) / comb(n_pop, n_sample)


def plan_sample_size(
    population_size: int,
    *,
    sampling_rate: float = DEFAULT_SAMPLING_RATE,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
) -> int:
    """抽出件数を決める。**母集団が1件以上なら、必ず1件以上返す。**

    ``int(3 * 0.3) == 0`` のような切り捨てで監査が空回りするのを防ぐ。
    """
    if population_size <= 0:
        return 0
    if not 0.0 < sampling_rate <= 1.0:
        raise AuditConfigurationError(
            f"抽出率は0より大きく1以下で指定してください: {sampling_rate}"
        )
    if minimum_sample < 1:
        raise AuditConfigurationError(
            "最小抽出件数は1以上にしてください。0にすると母集団があるのに"
            "1件も監査しない設定になり、監査が静かに空回りします"
        )
    by_rate = round(population_size * sampling_rate)
    return max(1, min(population_size, max(by_rate, minimum_sample)))


def run_provisional_audit(
    candidates: Sequence[AuditCandidate],
    ground_truth: Mapping[str, int] | Callable[[str], int | None],
    *,
    sampling_rate: float = DEFAULT_SAMPLING_RATE,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
    seed: int = 0,
    now: datetime | None = None,
) -> AuditReport:
    """階層2の母集団から抽出し、正解と照合して的中率を記録する。

    ``ground_truth`` は、正解を引けないときに ``None`` を返してよい
    (実運用の監査時点では正解は存在せず、人がこれから確認する)。
    その対象は ``unverifiable`` になり、**的中率の分母にも分子にも入らない。**
    """
    lookup = ground_truth.get if isinstance(ground_truth, Mapping) else ground_truth

    population = list(candidates)
    population_size = len(population)
    sample_size = plan_sample_size(
        population_size, sampling_rate=sampling_rate, minimum_sample=minimum_sample
    )
    recorded_at = now or datetime.now(timezone.utc)

    if population_size == 0:
        log = AuditLogEntry(
            recorded_at=recorded_at, population_size=0, sample_size=0,
            sampling_rate=sampling_rate, minimum_sample=minimum_sample, seed=seed,
            sampled_targets=(), status="no_population",
        )
        return AuditReport(
            status="no_population", population_size=0, sample_size=0,
            sampling_rate=sampling_rate, minimum_sample=minimum_sample, seed=seed,
            findings=(), log=log,
        )

    # 決定的に抽出する(同じシードなら同じ結果。報告の再現に必要)。
    # target 名で並べ替えてから抽出するので、呼び出し側が渡す順序に依存しない。
    ordered = sorted(population, key=lambda c: c.target)
    sampled = random.Random(seed).sample(ordered, sample_size)
    sampled.sort(key=lambda c: c.target)

    findings: list[AuditFinding] = []
    for candidate in sampled:
        truth = lookup(candidate.target)
        if truth is None:
            findings.append(AuditFinding(
                target=candidate.target, outcome="unverifiable",
                adopted_range=candidate.adopted_range, ground_truth=None,
                category=candidate.category,
                note="正解が無いため照合できない。人の確認待ち(一致ではない)",
            ))
        elif candidate.contains(truth):
            findings.append(AuditFinding(
                target=candidate.target, outcome="match",
                adopted_range=candidate.adopted_range, ground_truth=truth,
                category=candidate.category,
            ))
        else:
            findings.append(AuditFinding(
                target=candidate.target, outcome="mismatch",
                adopted_range=candidate.adopted_range, ground_truth=truth,
                category=candidate.category,
                note=(
                    f"階層2で自動採用した {candidate.adopted_range} の外に "
                    f"正解 {truth} があった"
                ),
            ))

    verifiable = sum(1 for f in findings if f.is_verifiable)
    status: AuditStatus = "verified" if verifiable > 0 else "awaiting_human"

    # v8 4-3節ルール3: 疑わしい1要素だけでなく、同じカテゴリの要素全体を
    # 監査対象にする。誤りが出たカテゴリを挙げる。
    expanded = tuple(sorted({f.category for f in findings if f.outcome == "mismatch"}))

    log = AuditLogEntry(
        recorded_at=recorded_at, population_size=population_size,
        sample_size=sample_size, sampling_rate=sampling_rate,
        minimum_sample=minimum_sample, seed=seed,
        sampled_targets=tuple(f.target for f in findings), status=status,
    )
    return AuditReport(
        status=status, population_size=population_size, sample_size=sample_size,
        sampling_rate=sampling_rate, minimum_sample=minimum_sample, seed=seed,
        findings=tuple(findings), log=log, expanded_categories=expanded,
    )


def expand_to_categories(
    candidates: Sequence[AuditCandidate], categories: Sequence[str]
) -> tuple[AuditCandidate, ...]:
    """v8 4-3節ルール3の拡大監査の対象を返す。

    誤りが見つかったカテゴリに属する要素を、**抽出されたかどうかに関わらず
    全件**返す。想定外の例外は1件だけ起きるとは限らないため。
    """
    wanted = set(categories)
    return tuple(c for c in candidates if c.category in wanted)


def collect_tier2_population(
    assessments: Mapping[str, "tuple[object, Sequence[object]]"],
    *,
    category_of: Callable[[str, Sequence[object]], str] | None = None,
) -> tuple[AuditCandidate, ...]:
    """ファイアウォールの判定結果から、階層2の母集団を組み立てる。

    ``assessments`` は ``{target: (FirewallDecision, [AxisEvidence, ...])}``。
    型注釈を緩くしてあるのは、``arbitration.axis_quality_firewall`` を
    import すると循環参照になるため(このモジュールは判定の**後ろ**に置く)。

    **階層2(``action == "provisional_audit"``)だけを母集団に入れる。**
    階層1は独立した強い軸2つの一致で確定しており、階層3は人が必ず見るので、
    どちらも抜き取り監査の対象ではない。ここに階層1や階層3が混ざると、
    的中率が薄まって階層2の品質が見えなくなる。
    """
    population: list[AuditCandidate] = []
    for target, (decision, evidences) in sorted(assessments.items()):
        if getattr(decision, "action", None) != "provisional_audit":
            continue
        adopted = getattr(decision, "confirmed_range", None)
        if adopted is None:
            # 階層2なのに採用範囲が無いのは、判定側の不整合。黙って飛ばさない。
            raise AuditConfigurationError(
                f"'{target}' は階層2ですが確定範囲がありません。"
                "自動採用した範囲が無ければ監査のしようがありません"
            )
        speaking = [e for e in evidences if getattr(e, "status", "") != "abstained"]
        first = speaking[0] if speaking else None
        method_id = getattr(first, "method_id", "") if first else ""
        axis_id = getattr(first, "axis_id", "") if first else ""
        unit = getattr(first, "unit", "") if first else ""
        category = (
            category_of(target, evidences) if category_of is not None
            else (method_id or axis_id or target)
        )
        population.append(AuditCandidate(
            target=target, adopted_range=adopted, unit=unit,
            category=category, axis_id=axis_id, method_id=method_id,
        ))
    return tuple(population)


def format_report(report: AuditReport, *, assumed_error_rate: float = 0.05) -> str:
    """報告書フォーマットに準じたテキストを返す。

    既存の報告書(`docs/*_report.md`)と同じく、**結論を先に書き、
    次に数値の表、最後に正直な留保点**という順にする。
    """
    lines: list[str] = []
    lines.append("## 階層2(仮採用+抜き取り監査)の抜き取り監査結果")
    lines.append("")

    if report.status == "no_population":
        lines.append("**階層2に分類された要素が1件も無いため、監査は行っていない。**")
        lines.append("")
        lines.append("これは「監査して全部当たった」とは**違う**状態である。")
        lines.append("")
        lines.append(f"```\n{report.log.format_line()}\n```")
        return "\n".join(lines)

    if report.status == "awaiting_human":
        lines.append(
            f"**{report.sample_size}件を抽出したが、正解が無いため1件も照合できていない。**"
        )
        lines.append("人の確認待ちであり、**的中率は出せない(「全件一致」ではない)。**")
    elif report.found_error:
        rate = report.hit_rate
        lines.append(
            f"**誤りを{report.mismatch_count}件検出した。** 的中率 "
            f"{rate:.1%}({report.match_count}/{report.verifiable_count})。"
        )
        lines.append(
            "**階層2の自動採用が、実際に誤った範囲を通していたということである。**"
        )
    else:
        rate = report.hit_rate
        lines.append(
            f"照合できた{report.verifiable_count}件はすべて一致した(的中率 {rate:.1%})。"
        )
        lines.append(
            "**ただしこれは「誤りが無い」ことの証明ではない。** 下の検出力を参照。"
        )

    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    lines.append(f"| 母集団(階層2の要素) | {report.population_size}件 |")
    lines.append(f"| 抽出件数 | {report.sample_size}件 |")
    lines.append(
        f"| 抽出率 | {report.sampling_rate:.0%}(最小{report.minimum_sample}件) |"
    )
    lines.append(f"| 照合できた件数 | {report.verifiable_count}件 |")
    lines.append(f"| 一致 | {report.match_count}件 |")
    lines.append(f"| **不一致** | **{report.mismatch_count}件** |")
    lines.append(f"| 照合できず(人の確認待ち) | {report.unverifiable_count}件 |")
    hit = report.hit_rate
    lines.append(f"| 的中率 | {'照合できた対象が無いため算出不能' if hit is None else f'{hit:.1%}'} |")
    lines.append(f"| 乱数シード | {report.seed} |")
    lines.append("")

    assumed_errors = max(1, round(report.population_size * assumed_error_rate))
    probability = report.detection_probability(assumed_errors)
    lines.append("### 検出力(この監査が見逃す確率)")
    lines.append("")
    lines.append(
        f"母集団{report.population_size}件のうち誤りが{assumed_errors}件"
        f"(誤り率{assumed_error_rate:.0%}の想定)あるとき、"
        f"{report.sample_size}件の抽出で1件以上捕まえられる確率は "
        f"**{probability:.1%}** である。"
    )
    lines.append("")
    lines.append(
        f"**裏を返せば {1 - probability:.1%} の確率で見逃す。** "
        "抜き取り監査は誤りの存在を確率的にしか検出できないので、"
        "「監査しているから安全」とは言えない。"
    )

    if report.expanded_categories:
        lines.append("")
        lines.append("### 拡大監査の対象(v8 4-3節ルール3)")
        lines.append("")
        lines.append(
            "誤りが見つかったカテゴリは、抽出されなかった要素も含めて全件を"
            "監査対象にする。想定外の例外が1件だけとは限らないため。"
        )
        lines.append("")
        for category in report.expanded_categories:
            lines.append(f"- `{category}`")

    lines.append("")
    lines.append("### 監査の実行記録")
    lines.append("")
    lines.append(f"```\n{report.log.format_line()}\n```")
    return "\n".join(lines)
