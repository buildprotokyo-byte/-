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
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

IntRange = tuple[int, int]

#: 既定の抽出率。おーちゃんの提案値(2026-09-21)。
DEFAULT_SAMPLING_RATE = 0.30

#: 既定の最小抽出件数。母集団がこれより小さければ全件を監査する。
#: 抽出率だけだと母集団が少ないときに0件になり、監査が静かに空回りする。
DEFAULT_MINIMUM_SAMPLE = 5

#: 抜き取り監査の対象になりうる階層。
#: **階層3は入らない。** 人が必ず見るので抜き取る意味が無く、母集団に混ぜると
#: 的中率が薄まるだけである。
AUDITABLE_TIERS: tuple[int, ...] = (1, 2)

#: ファイアウォールの ``action`` と階層番号の対応。
ACTION_TIERS: Mapping[str, int] = {
    "auto_confirm": 1,
    "provisional_audit": 2,
    "requires_review": 3,
}

#: 報告に出す階層の呼び名。
TIER_LABELS: Mapping[int, str] = {
    1: "階層1(自動確定)",
    2: "階層2(仮採用+抜き取り監査)",
    3: "階層3(人が確認)",
}

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

#: 抽出だけを行った段階の状態。**照合していないので的中率は無い。**
#: - no_population: その階層の要素が1件も無い(抜き取る対象が無い)
#: - sampled:       抜き取った
AuditPlanStatus = Literal["no_population", "sampled"]


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
    #: 人が確かめるための根拠(ページ番号・座標・読んだ元の文字列など)。
    #: **抜き取った一覧に根拠が付いていないと、人は何を確かめればいいのか
    #: 分からない。** 入口(`intake/drawing_intake.py`)が
    #: `DrawingFinding.provenance` をそのまま載せる。
    provenance: dict[str, Any] = field(default_factory=dict)
    #: この要素がどの階層で採用されたか。1=自動確定、2=仮採用+抜き取り監査。
    #: **階層をまたいで的中率を混ぜないための札である。** 既定を2にしてある
    #: のは、このモジュールが階層2専用として始まったため(既存の呼び出しが
    #: そのまま階層2として通る)。
    tier: int = 2

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
    #: どの階層の要素を監査した結果か。集計を階層ごとに分けるために残す。
    tier: int = 2

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
    #: どの階層の監査だったか。**記録にこれが無いと、あとから
    #: 「階層1の監査が走ったのか」を確かめる手段が無い。**
    tier: int = 2

    def format_line(self) -> str:
        stamp = self.recorded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"[{stamp}] provisional_audit "
            f"population={self.population_size} sample={self.sample_size} "
            f"rate={self.sampling_rate:.2f} min={self.minimum_sample} "
            f"seed={self.seed} tier={self.tier} status={self.status} "
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
    #: この報告が対象にした階層。**1つの報告は必ず1つの階層だけを見る。**
    tier: int = 2

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


@dataclass(frozen=True)
class TierAuditPlan:
    """**1つの階層**について、誰を抜き取ったか。

    **正解には触れていない。** 一致・不一致も的中率もここには無い
    (`score_audit_plan()` が別に付ける)。分けてあるのは、本番の入口
    (`intake/drawing_intake.read_drawing()`)が「正解データはここでは一切
    読まない」と約束しているためで、入口が呼ぶのはこの抽出までである。
    """

    tier: int
    status: AuditPlanStatus
    population_size: int
    sample_size: int
    sampling_rate: float
    minimum_sample: int
    seed: int
    planned_at: datetime
    sampled: tuple[AuditCandidate, ...]

    def as_log_dict(self) -> dict[str, Any]:
        """記録に残す形。**抜き取った1件ごとに根拠を添える。**"""
        return {
            "population": self.population_size,
            "sample": self.sample_size,
            "rate": self.sampling_rate,
            "minimum": self.minimum_sample,
            "status": self.status,
            "sampled": [
                {
                    "target": item.target,
                    "adopted_range": [item.adopted_range[0], item.adopted_range[1]],
                    "unit": item.unit,
                    "category": item.category,
                    "axis_id": item.axis_id,
                    "method_id": item.method_id,
                    "tier": item.tier,
                    "provenance": dict(item.provenance),
                }
                for item in self.sampled
            ],
        }


@dataclass(frozen=True)
class TieredAuditPlan:
    """階層ごとの抽出結果と、それがどの図面に対するものか。

    **指紋を持たせてある理由。** 案件をまたいで母集団を積み上げるとき、
    同じ図面の同じ読みに対する監査を二重に数えていないかを後から判定する
    必要がある。ファイル名は匿名化で変わるうえ案件名が入ることがあるので、
    中身の sha256 を使う(`intake/drawing_intake.file_fingerprint()`)。
    """

    plans: Mapping[int, TierAuditPlan]
    planned_at: datetime
    seed: int
    case_id: str = ""
    source_fingerprint: str = ""
    start_kit_fingerprint: str = ""

    def plan_for(self, tier: int) -> TierAuditPlan | None:
        return self.plans.get(tier)

    @property
    def audited_tiers(self) -> tuple[int, ...]:
        return tuple(sorted(self.plans))

    @property
    def total_sample_size(self) -> int:
        return sum(plan.sample_size for plan in self.plans.values())

    @property
    def has_population(self) -> bool:
        return any(plan.population_size > 0 for plan in self.plans.values())

    def as_log_dict(self) -> dict[str, Any]:
        """そのまま JSON に書ける記録。

        **案件の情報なのでリポジトリには置かない**(`intake/case_answers.py`
        の `AnswerStore` と同じ約束)。書き出す先は設定で渡されたパスだけで、
        既定値を持たせない。
        """
        return {
            "case_id": self.case_id,
            "recorded_at": self.planned_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_fingerprint": self.source_fingerprint,
            "start_kit_fingerprint": self.start_kit_fingerprint,
            "seed": self.seed,
            "tiers": {
                str(tier): plan.as_log_dict()
                for tier, plan in sorted(self.plans.items())
            },
        }


class GroundTruthSource(Protocol):
    """監査の正解を引く口。

    **正解が無ければ ``None`` を返してよい。** 実運用の監査時点では正解は
    存在しない(だから人が確認する)。``None`` の対象は ``unverifiable``
    になり、的中率の分母にも分子にも入らない。
    """

    def lookup(self, case_id: str, target: str) -> int | None: ...


@dataclass(frozen=True)
class NoGroundTruth:
    """**常に ``None`` を返す既定の引き口。**

    正解データの記入者がまだ決まっていない(2026-09-22)。決まるまでは
    これを使う。このとき監査の出力は的中率ではなく、**人が確かめるべき
    対象の一覧**になる(`docs/audit_production_integration_design.md` 3-3節)。
    """

    def lookup(self, case_id: str, target: str) -> int | None:
        return None


def _single_tier(candidates: Sequence[AuditCandidate], tier: int | None) -> int:
    """母集団の階層を1つに決める。**混ざっていたら止める。**"""
    tiers_present = {c.tier for c in candidates}
    if len(tiers_present) > 1:
        raise AuditConfigurationError(
            f"階層が混ざった母集団は監査できません: {sorted(tiers_present)}。"
            "1つの的中率に混ぜると、件数の多い階層が少ない階層の誤りを薄めます。"
            "階層ごとに分けて集計する run_tiered_audit を使ってください"
        )
    if tier is None:
        return tiers_present.pop() if tiers_present else 2
    if tiers_present and tiers_present != {tier}:
        raise AuditConfigurationError(
            f"指定された階層 {tier} と母集団の階層 {sorted(tiers_present)} が違います"
        )
    return tier


def plan_provisional_audit(
    candidates: Sequence[AuditCandidate],
    *,
    sampling_rate: float = DEFAULT_SAMPLING_RATE,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
    seed: int = 0,
    now: datetime | None = None,
    tier: int | None = None,
) -> TierAuditPlan:
    """**1つの階層**の母集団から抜き取る。**正解には触らない。**

    決定的に抽出する(同じシードなら同じ結果)。target 名で並べ替えてから
    抽出するので、呼び出し側が渡す順序に依存しない。
    """
    population = list(candidates)
    population_size = len(population)
    resolved_tier = _single_tier(population, tier)
    planned_at = now or datetime.now(timezone.utc)
    sample_size = plan_sample_size(
        population_size, sampling_rate=sampling_rate, minimum_sample=minimum_sample
    )

    if population_size == 0:
        return TierAuditPlan(
            tier=resolved_tier, status="no_population", population_size=0,
            sample_size=0, sampling_rate=sampling_rate,
            minimum_sample=minimum_sample, seed=seed, planned_at=planned_at,
            sampled=(),
        )

    ordered = sorted(population, key=lambda c: c.target)
    sampled = random.Random(seed).sample(ordered, sample_size)
    sampled.sort(key=lambda c: c.target)
    return TierAuditPlan(
        tier=resolved_tier, status="sampled", population_size=population_size,
        sample_size=sample_size, sampling_rate=sampling_rate,
        minimum_sample=minimum_sample, seed=seed, planned_at=planned_at,
        sampled=tuple(sampled),
    )


def _resolve_lookup(
    ground_truth: "Mapping[str, int] | Callable[[str], int | None] | GroundTruthSource",
    case_id: str,
) -> Callable[[str], int | None]:
    """正解の引き口を、対象名1つを取る関数にそろえる。"""
    if isinstance(ground_truth, Mapping):
        return ground_truth.get
    lookup = getattr(ground_truth, "lookup", None)
    if lookup is not None:
        return lambda target: lookup(case_id, target)
    return ground_truth  # type: ignore[return-value]


def score_tier_plan(
    plan: TierAuditPlan,
    ground_truth: "Mapping[str, int] | Callable[[str], int | None] | GroundTruthSource",
    *,
    case_id: str = "",
) -> AuditReport:
    """抜き取った1件ずつを正解と照合する。**抽出はやり直さない。**

    ``ground_truth`` は、正解を引けないときに ``None`` を返してよい
    (実運用の監査時点では正解は存在せず、人がこれから確認する)。
    その対象は ``unverifiable`` になり、**的中率の分母にも分子にも入らない。**
    """
    lookup = _resolve_lookup(ground_truth, case_id)
    tier = plan.tier

    if plan.status == "no_population":
        log = AuditLogEntry(
            recorded_at=plan.planned_at, population_size=0, sample_size=0,
            sampling_rate=plan.sampling_rate, minimum_sample=plan.minimum_sample,
            seed=plan.seed, sampled_targets=(), status="no_population", tier=tier,
        )
        return AuditReport(
            status="no_population", population_size=0, sample_size=0,
            sampling_rate=plan.sampling_rate, minimum_sample=plan.minimum_sample,
            seed=plan.seed, findings=(), log=log, tier=tier,
        )

    findings: list[AuditFinding] = []
    for candidate in plan.sampled:
        truth = lookup(candidate.target)
        if truth is None:
            findings.append(AuditFinding(
                target=candidate.target, outcome="unverifiable",
                adopted_range=candidate.adopted_range, ground_truth=None,
                category=candidate.category, tier=candidate.tier,
                note="正解が無いため照合できない。人の確認待ち(一致ではない)",
            ))
        elif candidate.contains(truth):
            findings.append(AuditFinding(
                target=candidate.target, outcome="match",
                adopted_range=candidate.adopted_range, ground_truth=truth,
                category=candidate.category, tier=candidate.tier,
            ))
        else:
            findings.append(AuditFinding(
                target=candidate.target, outcome="mismatch",
                adopted_range=candidate.adopted_range, ground_truth=truth,
                category=candidate.category, tier=candidate.tier,
                note=(
                    f"階層{candidate.tier}で自動採用した "
                    f"{candidate.adopted_range} の外に正解 {truth} があった"
                ),
            ))

    verifiable = sum(1 for f in findings if f.is_verifiable)
    status: AuditStatus = "verified" if verifiable > 0 else "awaiting_human"

    # v8 4-3節ルール3: 疑わしい1要素だけでなく、同じカテゴリの要素全体を
    # 監査対象にする。誤りが出たカテゴリを挙げる。
    expanded = tuple(sorted({f.category for f in findings if f.outcome == "mismatch"}))

    log = AuditLogEntry(
        recorded_at=plan.planned_at, population_size=plan.population_size,
        sample_size=plan.sample_size, sampling_rate=plan.sampling_rate,
        minimum_sample=plan.minimum_sample, seed=plan.seed,
        sampled_targets=tuple(f.target for f in findings), status=status,
        tier=tier,
    )
    return AuditReport(
        status=status, population_size=plan.population_size,
        sample_size=plan.sample_size, sampling_rate=plan.sampling_rate,
        minimum_sample=plan.minimum_sample, seed=plan.seed,
        findings=tuple(findings), log=log, expanded_categories=expanded,
        tier=tier,
    )


def run_provisional_audit(
    candidates: Sequence[AuditCandidate],
    ground_truth: "Mapping[str, int] | Callable[[str], int | None] | GroundTruthSource",
    *,
    sampling_rate: float = DEFAULT_SAMPLING_RATE,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
    seed: int = 0,
    now: datetime | None = None,
    tier: int | None = None,
    case_id: str = "",
) -> AuditReport:
    """**1つの階層**を抜き取って照合する。抽出と照合の合成。

    **階層が混ざった母集団は受け付けない。** 受け付けると的中率が1つに
    混ざり、件数の多い階層が少ない階層の誤りを薄めて隠す。これは
    ``collect_tier2_population`` が階層2だけを集めていた理由そのものである。
    複数の階層を監査したいときは :func:`run_tiered_audit` を使う。
    """
    plan = plan_provisional_audit(
        candidates, sampling_rate=sampling_rate, minimum_sample=minimum_sample,
        seed=seed, now=now, tier=tier,
    )
    return score_tier_plan(plan, ground_truth, case_id=case_id)


def expand_to_categories(
    candidates: Sequence[AuditCandidate], categories: Sequence[str]
) -> tuple[AuditCandidate, ...]:
    """v8 4-3節ルール3の拡大監査の対象を返す。

    誤りが見つかったカテゴリに属する要素を、**抽出されたかどうかに関わらず
    全件**返す。想定外の例外は1件だけ起きるとは限らないため。
    """
    wanted = set(categories)
    return tuple(c for c in candidates if c.category in wanted)


def collect_audit_population(
    assessments: Mapping[str, "tuple[object, Sequence[object]]"],
    *,
    tiers: Sequence[int] = (2,),
    category_of: Callable[[str, Sequence[object]], str] | None = None,
) -> tuple[AuditCandidate, ...]:
    """ファイアウォールの判定結果から、``tiers`` の階層の母集団を組み立てる。

    ``assessments`` は ``{target: (FirewallDecision, [AxisEvidence, ...])}``。
    型注釈を緩くしてあるのは、``arbitration.axis_quality_firewall`` を
    import すると循環参照になるため(このモジュールは判定の**後ろ**に置く)。

    **既定は階層2だけである。** 階層1を監査するかどうか、するとしてどの割合で
    するかは運用の判断なので、明示的に ``tiers=(1, 2)`` と書かないと入らない。

    元の実装が階層2だけを集めていたのは「階層1・階層3を混ぜると的中率が
    薄まり、階層2の品質が見えなくなる」ためである。**薄まるのは的中率を1つの
    数にまとめたときだけで、階層ごとに分けて数えるかぎり起きない。** 集めた
    要素には ``AuditCandidate.tier`` で階層の札を付け、:func:`run_tiered_audit`
    が階層ごとに別々の母集団として扱う。

    **階層3は入れられない。** 人が必ず見るので抜き取る意味が無い。
    """
    wanted = tuple(dict.fromkeys(tiers))
    if not wanted:
        raise AuditConfigurationError("監査する階層を1つ以上指定してください")
    unsupported = [t for t in wanted if t not in AUDITABLE_TIERS]
    if unsupported:
        raise AuditConfigurationError(
            f"抜き取り監査の対象にできない階層です: {unsupported}。"
            f"対象にできるのは {list(AUDITABLE_TIERS)} だけです"
            "(階層3は人が必ず見るので抜き取る意味がなく、母集団に混ぜると"
            "的中率が薄まります)"
        )

    population: list[AuditCandidate] = []
    for target, (decision, evidences) in sorted(assessments.items()):
        action = getattr(decision, "action", None)
        tier = ACTION_TIERS.get(action) if isinstance(action, str) else None
        if tier is None:
            continue
        declared = getattr(decision, "tier", None)
        if declared is not None and declared != tier:
            # 階層番号と action が食い違う判定を黙って受け入れると、
            # 監査の集計が実際とは違う階層に入る。
            raise AuditConfigurationError(
                f"'{target}' は action={action!r}(階層{tier})なのに "
                f"tier={declared} と申告されています。どちらかが誤っています"
            )
        if tier not in wanted:
            continue
        adopted = getattr(decision, "confirmed_range", None)
        if adopted is None:
            # 自動採用された階層なのに採用範囲が無いのは、判定側の不整合。
            # 黙って飛ばさない。
            raise AuditConfigurationError(
                f"'{target}' は階層{tier}ですが確定範囲がありません。"
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
            category=category, axis_id=axis_id, method_id=method_id, tier=tier,
        ))
    return tuple(population)


def collect_tier2_population(
    assessments: Mapping[str, "tuple[object, Sequence[object]]"],
    *,
    category_of: Callable[[str, Sequence[object]], str] | None = None,
) -> tuple[AuditCandidate, ...]:
    """階層2だけの母集団を組み立てる。

    :func:`collect_audit_population` の既定と同じもの。階層を広げる前から
    ある呼び出し口を、名前のまま残してある。
    """
    return collect_audit_population(assessments, tiers=(2,), category_of=category_of)


def format_report(report: AuditReport, *, assumed_error_rate: float = 0.05) -> str:
    """報告書フォーマットに準じたテキストを返す。

    既存の報告書(`docs/*_report.md`)と同じく、**結論を先に書き、
    次に数値の表、最後に正直な留保点**という順にする。
    """
    label = TIER_LABELS.get(report.tier, f"階層{report.tier}")
    lines: list[str] = []
    lines.append(f"## {label}の抜き取り監査結果")
    lines.append("")

    if report.status == "no_population":
        lines.append(
            f"**{label}に分類された要素が1件も無いため、監査は行っていない。**"
        )
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
            f"**{label}の自動採用が、実際に誤った範囲を通していたということである。**"
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
    lines.append(f"| 母集団({label}の要素) | {report.population_size}件 |")
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


# =====================================================================
# 階層ごとの抜き取り監査
# =====================================================================
#
# 階層2だけを監査していたときの前提は「階層1・階層3を混ぜると的中率が薄まり、
# 階層2の品質が見えなくなる」だった。**薄まるのは的中率を1つの数にまとめた
# ときだけである。** 階層ごとに母集団・抽出件数・的中率・検出力を分けて数える
# かぎり、この前提は壊れない。だからここでは
#
# - 階層ごとに :class:`AuditReport` を1つずつ作り、
# - **階層をまたいだ的中率を出す入口は作らない**(``TieredAuditReport`` に
#   ``hit_rate`` は無い)。
#
# 一方で階層1と階層2は性質が違う。**階層1は ``auto_confirm`` で人が一度も
# 見ない。** 階層2は抜き取られれば人が見る。見逃したときの重さが違うので、
# 抽出率は階層ごとに別々に決められる必要がある。


@dataclass(frozen=True)
class TierAuditPolicy:
    """1つの階層をどの割合で抜き取るか。

    値の検査を**生成時**に行う。実行時まで遅らせると、方針として書いた値が
    おかしくても監査が走るまで気づけない。
    """

    sampling_rate: float = DEFAULT_SAMPLING_RATE
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE

    def __post_init__(self) -> None:
        if not 0.0 < self.sampling_rate <= 1.0:
            raise AuditConfigurationError(
                f"抽出率は0より大きく1以下で指定してください: {self.sampling_rate}"
            )
        if self.minimum_sample < 1:
            raise AuditConfigurationError(
                "最小抽出件数は1以上にしてください。0にすると母集団があるのに"
                "1件も監査しない設定になり、監査が静かに空回りします"
            )

    def sample_size_for(self, population_size: int) -> int:
        return plan_sample_size(
            population_size,
            sampling_rate=self.sampling_rate,
            minimum_sample=self.minimum_sample,
        )


#: **引き継ぎの約束(2026-09-22、おーちゃんの指示)。**
#:
#: **この検査が実際に動き始めたら — 最初に1件以上を抜き取って検査したら —
#: その結果を報告すること。**
#:
#: 2026-09-22 時点の母集団は 0 件である(図面 PDF は 1 つのデータ源なので
#: 独立した強い軸が 2 つ揃わず、全対象が階層3になる)。寸法の数字とスキャンの
#: 文字が読めるようになって自動確定が出はじめた瞬間に、最初の 1 件が出る。
#: **そのときが最初の報告のタイミングである。** 0 件の報告は要らない。
#:
#: 正解をどこから引くかは**引き続き保留**なので、最初の検査でも
#: `hit_rate` は `None`、`status` は `awaiting_human` になる。
#: **そのとき報告するのは的中率ではなく「人が確かめるべき対象の一覧」である**
#: (`docs/audit_production_integration_design.md` 3-3節)。
FIRST_AUDIT_MUST_BE_REPORTED = True

#: 既定の階層ごとの方針。
#:
#: **階層2は従来どおり抽出率30%・最小5件。この値は変えていない。**
#:
#: **階層1は入っていない。** 階層1をどの割合で監査するかは運用の判断
#: (人手コストと、人が一度も見ない階層を放置する危険の釣り合い)であり、
#: コードに既定値を置いてしまうと、値が決まらないまま運用に入る。
#: 監査したいときは呼び出し側が ``{1: TierAuditPolicy(...), 2: ...}`` と
#: 明示する。
DEFAULT_TIER_POLICIES: Mapping[int, TierAuditPolicy] = MappingProxyType(
    {2: TierAuditPolicy()}
)


@dataclass(frozen=True)
class TieredAuditReport:
    """階層ごとの抜き取り監査の結果をまとめたもの。

    **``hit_rate`` を意図的に持たない。** 階層をまたいだ的中率は、件数の多い
    階層が少ない階層の誤りを薄めて隠す。的中率は ``report_for(tier).hit_rate``
    で階層ごとに読む。
    """

    reports: Mapping[int, AuditReport]

    def report_for(self, tier: int) -> AuditReport | None:
        return self.reports.get(tier)

    @property
    def audited_tiers(self) -> tuple[int, ...]:
        return tuple(sorted(self.reports))

    @property
    def found_error(self) -> bool:
        """どれか1つの階層でも誤りを捕まえたか。

        これは的中率ではなく「誤りが出たか出ていないか」なので、階層を
        またいでも薄まらない。
        """
        return any(r.found_error for r in self.reports.values())

    @property
    def expanded_categories(self) -> tuple[str, ...]:
        """v8 4-3節ルール3で拡大監査の対象になるカテゴリ。

        **階層をまたいで集める。** 系統誤差は手法から出るので、階層2で誤りが
        出た手法を使っている階層1の要素も疑わしい。
        **返すだけで、自動で階層を下げたりはしない。**
        """
        categories: set[str] = set()
        for report in self.reports.values():
            categories.update(report.expanded_categories)
        return tuple(sorted(categories))


def _checked_policies(
    policies: Mapping[int, TierAuditPolicy],
    population: Sequence[AuditCandidate],
) -> Mapping[int, TierAuditPolicy]:
    """方針と母集団の食い違いを、走らせる前に止める。"""
    if not policies:
        raise AuditConfigurationError("監査する階層の方針を1つ以上指定してください")
    unsupported = [t for t in policies if t not in AUDITABLE_TIERS]
    if unsupported:
        raise AuditConfigurationError(
            f"抜き取り監査の対象にできない階層です: {sorted(unsupported)}。"
            f"対象にできるのは {list(AUDITABLE_TIERS)} だけです"
        )
    unplanned = sorted({c.tier for c in population} - set(policies))
    if unplanned:
        raise AuditConfigurationError(
            f"母集団に階層 {unplanned} の要素がありますが、その階層の方針が"
            "ありません。黙って監査対象から外すと、監査しているつもりで"
            "1件も監査されていない状態になります。"
            f"policies に階層 {unplanned} を足すか、母集団から外してください"
        )
    return policies


def plan_tiered_audit(
    candidates: Sequence[AuditCandidate],
    *,
    policies: Mapping[int, TierAuditPolicy] = DEFAULT_TIER_POLICIES,
    seed: int = 0,
    now: datetime | None = None,
    case_id: str = "",
    source_fingerprint: str = "",
    start_kit_fingerprint: str = "",
) -> TieredAuditPlan:
    """階層ごとに母集団を分けて抜き取る。**正解には触らない。**

    **この関数は正解を引数に取らない。** 取れるようにすると、本番の入口から
    正解を渡す経路がそこにできる。入口
    (`intake/drawing_intake.read_drawing()`)が呼ぶのはここまでで、照合は
    :func:`score_audit_plan` が別に行う
    (`docs/audit_production_integration_design.md` 3-1節)。

    ``policies`` に挙げた階層それぞれについて抽出を1回ずつ行う。
    **要素が0件の階層も結果を作る**(``no_population``)。「方針はあるのに
    1件も抜き取られていない」ことを、あとから確認できるようにするため。

    **母集団に、方針を決めていない階層が混ざっていたら止める。** 黙って
    捨てると「階層1も監査しているつもり」で1件も監査されず、集計表を見ても
    気づけない。

    乱数シードは階層ごとにずらさない。母集団が階層ごとに別のリストなので、
    同じシードでも抽出は独立している。**ずらさないことで、階層2だけを渡した
    ときの抽出結果が従来と1件も変わらない。**
    """
    population = list(candidates)
    checked = _checked_policies(policies, population)
    planned_at = now or datetime.now(timezone.utc)
    plans = {
        tier: plan_provisional_audit(
            [c for c in population if c.tier == tier],
            sampling_rate=policy.sampling_rate,
            minimum_sample=policy.minimum_sample,
            seed=seed,
            now=planned_at,
            tier=tier,
        )
        for tier, policy in sorted(checked.items())
    }
    return TieredAuditPlan(
        plans=MappingProxyType(plans), planned_at=planned_at, seed=seed,
        case_id=case_id, source_fingerprint=source_fingerprint,
        start_kit_fingerprint=start_kit_fingerprint,
    )


def score_audit_plan(
    plan: TieredAuditPlan,
    ground_truth: "Mapping[str, int] | Callable[[str], int | None] | GroundTruthSource",
) -> TieredAuditReport:
    """抜き取った結果を正解と照合する。**抽出はやり直さない。**

    正解が引けない対象は ``unverifiable`` になり、その階層の ``hit_rate`` は
    ``None``、``status`` は ``awaiting_human`` になる。
    **そのとき監査の出力は的中率ではなく、人が確かめるべき対象の一覧である。**
    """
    return TieredAuditReport(
        reports=MappingProxyType({
            tier: score_tier_plan(one, ground_truth, case_id=plan.case_id)
            for tier, one in sorted(plan.plans.items())
        })
    )


def run_tiered_audit(
    candidates: Sequence[AuditCandidate],
    ground_truth: "Mapping[str, int] | Callable[[str], int | None] | GroundTruthSource",
    *,
    policies: Mapping[int, TierAuditPolicy] = DEFAULT_TIER_POLICIES,
    seed: int = 0,
    now: datetime | None = None,
    case_id: str = "",
) -> TieredAuditReport:
    """階層ごとに抜き取って照合する。抽出と照合の合成。

    抽出だけが要る呼び出し(本番の入口)は :func:`plan_tiered_audit` を使う。
    """
    plan = plan_tiered_audit(
        candidates, policies=policies, seed=seed, now=now, case_id=case_id
    )
    return score_audit_plan(plan, ground_truth)


def format_tiered_report(
    report: TieredAuditReport, *, assumed_error_rate: float = 0.05
) -> str:
    """階層ごとの監査結果を、階層ごとに分けて書く。

    **階層をまたいだ的中率は書かない。** 書けばそれが一人歩きして、件数の
    多い階層が少ない階層の誤りを隠す。
    """
    lines: list[str] = []
    lines.append("# 抜き取り監査の結果(階層別)")
    lines.append("")
    lines.append(
        "**的中率は階層ごとに出している。階層をまたいだ的中率は出さない。** "
        "まとめてしまうと、件数の多い階層が少ない階層の誤りを薄めて隠すためである。"
    )
    lines.append("")

    tiers = report.audited_tiers
    if not tiers:
        lines.append("監査した階層が1つも無い。")
        return "\n".join(lines)

    lines.append("| 階層 | 母集団 | 抽出 | 照合できた | 不一致 | 的中率 |")
    lines.append("|---|---|---|---|---|---|")
    for tier in tiers:
        one = report.reports[tier]
        hit = one.hit_rate
        lines.append(
            f"| {TIER_LABELS.get(tier, f'階層{tier}')} | {one.population_size}件 "
            f"| {one.sample_size}件 | {one.verifiable_count}件 "
            f"| **{one.mismatch_count}件** "
            f"| {'算出不能' if hit is None else f'{hit:.1%}'} |"
        )
    lines.append("")

    if report.found_error:
        lines.append(
            "**誤りを検出した階層がある。** どの階層かは上の表と下の節を参照。"
        )
    else:
        lines.append(
            "どの階層でも不一致は出ていない。**ただしこれは「誤りが無い」ことの"
            "証明ではない。** 各階層の検出力を参照。"
        )
    lines.append("")

    for tier in tiers:
        lines.append(format_report(report.reports[tier], assumed_error_rate=assumed_error_rate))
        lines.append("")

    if report.expanded_categories:
        lines.append("## 階層をまたいだ拡大監査の対象(v8 4-3節ルール3)")
        lines.append("")
        lines.append(
            "系統誤差は手法から出るので、誤りが見つかった手法を使っている要素は"
            "**別の階層のものも**拡大監査の対象にする。"
            "**対象として挙げるだけで、自動で階層を下げたりはしない。**"
        )
        lines.append("")
        for category in report.expanded_categories:
            lines.append(f"- `{category}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
