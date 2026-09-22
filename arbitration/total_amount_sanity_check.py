"""出口検査: 見積もり合計金額の桁違いを、業界統計の分布と照らして捕まえる。

位置づけ(`docs/design_v8.md` 5-2節)
--------------------------------------
軸間照合・3段階確信度階層・キラークエスチョンは、すべて**個別の対象要素の
数量**を扱う。この検査はそれらの後ろに置かれ、**数量が確定し単価を掛けて
見積もり合計金額が出た後に、1回だけ走る**。

もともと「業界一般統計軸」として `AxisQualityFirewall` に参加させていたが、
取得できた公開統計が持つのは個別工事の**受注額(万円/件)の分布**だけで、
部材の数量には答えられず、粒度も「1件の工事全体」であって対象要素ではない。
数値だけを重ねると、金額のレンジが個数を「支持」して確信度階層を押し上げる
という誤判定が実際に起きた(`docs/proposal_industry_statistics_repositioning.md`
1-3節の実測)。そのため軸カタログから外し、この出口検査に位置づけ直した。

**権限は非対称である。**
--------------------------------------
この検査は確信度階層を**下げること(階層3=要確認へ落とすこと)だけ**ができ、
**上げることは絶対にできない。** 分布の真ん中に入っていることは正しさの
証拠ではなく、「桁が合っている」以上の意味を持たない。そのため
``TotalAmountVerdict`` は「支持する」という結論を型として持たず、
``forces_review`` が True になる経路しか用意していない。

**検出できる範囲は限定的で、そのことを型で表す。**
--------------------------------------
* 上振れ(高すぎる): 検出する。閾値は分布の第99パーセンタイル(用途により
  中央値の17〜79倍)。「桁違い=10倍」は閾値にならない。中央値の10倍を
  超える工事が実在で 2.7〜11.9% あるため
* **下振れ(安すぎる): 検出しない。** 最下位階級が全体の 59〜82% を占め、
  その内側の分布は統計に載っていない。``low``(第5パーセンタイル)は
  階級内一様分布の仮定だけから出た値であり、実データの裏付けが無い
* **新築工事: 適用外。** この統計はリフォーム・リニューアル工事の受注額
* **1件の工事の受注額**なので、複数工事をまとめた見積もり・1件を分割した
  見積もりには適用できない

根拠の数値と集計方法は `benchmarks/analyze_amount_distribution.py` で
再現できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from axes.industry_statistics_axis.snapshot import IndustryMetric, StatisticsSnapshot

#: この検査が扱える金額の単位。統計の単位(万円/件)と一致させる。
#: 呼び出し側は合計金額をこの単位に変換してから渡す。
EXPECTED_UNIT = "万円"

#: 工事区分。統計がリフォーム・リニューアルの受注額なので、それ以外は棄権する。
WorkKind = Literal["renovation", "new_construction", "unknown"]

VerdictStatus = Literal[
    "within_distribution",  # 分布の中。何の支持も与えない(桁が合っているだけ)
    "high_outlier",  # 第99パーセンタイル超。要確認へ落とす
    "beyond_statistics",  # 上限の無い最上位階級。統計はこれ以上言えない
    "abstained",  # 判定材料が無い。下振れ・新築・用途不明はすべてここ
]


@dataclass(frozen=True)
class TotalAmountVerdict:
    """1件の見積もり合計金額に対する検査結果。

    ``forces_review`` が True になるのは ``high_outlier`` と
    ``beyond_statistics`` だけ。**階層を上げるフィールドは存在しない。**
    """

    status: VerdictStatus
    forces_review: bool
    reason: str
    evidence: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 注: 括弧は必須。`a != b in c` は連鎖比較として解釈され、
        # `(a != b) and (b in c)` になってしまう。
        if self.forces_review != (self.status in ("high_outlier", "beyond_statistics")):
            raise ValueError(
                "forces_review は high_outlier / beyond_statistics のときだけ True。"
                f"status={self.status} forces_review={self.forces_review}"
            )

    def applied_tier(self, tier: int) -> int:
        """この検査を通した後の確信度階層。

        **下げることしかしない。** ``forces_review`` が False のときは、
        与えられた階層をそのまま返す(分布の中に入っていることを理由に
        階層を上げてはならない、という 5-2節の非対称性の実装)。
        """
        return 3 if self.forces_review else tier


def _abstain(reason: str, **evidence: object) -> TotalAmountVerdict:
    return TotalAmountVerdict(
        status="abstained", forces_review=False, reason=reason, evidence=dict(evidence)
    )


def check_total_amount(
    snapshot: StatisticsSnapshot,
    total_amount: float,
    *,
    metric_name: str,
    unit: str = EXPECTED_UNIT,
    work_kind: WorkKind = "renovation",
) -> TotalAmountVerdict:
    """見積もり合計金額を、用途を合わせた統計の分布と照らす。

    :param total_amount: 1件の工事の見積もり合計金額。単位は ``unit``。
    :param metric_name: 用途を指定するスナップショットの項目名
        (例 ``"個別工事の受注額:住宅:住宅 計"``)。
    :param unit: ``total_amount`` の単位。``EXPECTED_UNIT`` 以外は棄権する
        (単位を黙って読み替えない。これが今回の位置づけ修正の発端そのもの)。
    :param work_kind: 工事区分。``"renovation"`` 以外は棄権する。

    判定材料が無い場合は、推測で埋めずに ``abstained`` を返す。
    """
    if unit != EXPECTED_UNIT:
        return _abstain(
            f"合計金額の単位が『{unit}』で、統計の単位『{EXPECTED_UNIT}』と一致しないため"
            "判定しない(単位を読み替えることはしない)",
            given_unit=unit,
            expected_unit=EXPECTED_UNIT,
        )
    if work_kind != "renovation":
        return _abstain(
            "この統計はリフォーム・リニューアル工事の受注額なので、"
            f"工事区分『{work_kind}』には適用できない",
            work_kind=work_kind,
        )
    if snapshot.is_placeholder:
        return _abstain(
            "スナップショットが中身の無いプレースホルダーのため判定しない",
            source_name=snapshot.source_name,
        )

    metric = snapshot.metrics.get(metric_name)
    if metric is None:
        return _abstain(
            f"用途『{metric_name}』に対応する分布がスナップショットに無いため判定しない",
            metric_name=metric_name,
            available=len(snapshot.metrics),
        )
    if metric.unit != EXPECTED_UNIT + "/件":
        return _abstain(
            f"項目『{metric_name}』の単位が『{metric.unit}』で、"
            "1件あたりの金額として扱えないため判定しない",
            metric_unit=metric.unit,
        )
    if total_amount < 0:
        return _abstain("合計金額が負のため判定しない", total_amount=total_amount)

    evidence = _evidence_for(snapshot, metric, total_amount)

    beyond = metric.beyond_statistics_threshold
    if beyond is not None and total_amount >= beyond:
        return TotalAmountVerdict(
            status="beyond_statistics",
            forces_review=True,
            reason=(
                f"合計金額 {total_amount:,.1f}万円 は、統計の最上位階級"
                f"({beyond:,.0f}万円以上)に落ちる"
                f"{_median_ratio_text(metric, total_amount)}。"
                "この階級には上限が無く、統計はこれ以上何も言えない。"
                "桁違いの可能性を人が確認する必要がある"
            ),
            evidence=evidence,
        )

    outlier = metric.high_outlier_threshold
    if outlier is not None and total_amount > outlier:
        return TotalAmountVerdict(
            status="high_outlier",
            forces_review=True,
            reason=(
                f"合計金額 {total_amount:,.1f}万円 は、この用途の第99パーセンタイル"
                f"({outlier:,.1f}万円)を超えている"
                f"{_median_ratio_text(metric, total_amount)}。"
                "100件に1件より希少な水準のため、人が確認する必要がある"
            ),
            evidence=evidence,
        )

    if outlier is None and beyond is None:
        return _abstain(
            f"用途『{metric_name}』には上振れの閾値が無いため判定しない"
            "(分位点が上限の無い階級に落ちる用途)",
            metric_name=metric_name,
        )

    return TotalAmountVerdict(
        status="within_distribution",
        forces_review=False,
        reason=(
            f"合計金額 {total_amount:,.1f}万円 は分布の中に収まっている。"
            "**桁が合っていることしか意味せず、正しさの証拠にはならない**"
            "(この検査は確信度階層を上げない)"
        ),
        evidence=evidence,
    )


def _median_ratio_text(metric: IndustryMetric, total_amount: float) -> str:
    if not metric.typical:
        return ""
    return f"(中央値 {metric.typical:,.1f}万円 の {total_amount / metric.typical:,.1f} 倍)"


def _evidence_for(
    snapshot: StatisticsSnapshot, metric: IndustryMetric, total_amount: float
) -> dict[str, object]:
    return {
        "source_name": snapshot.source_name,
        "source_url": snapshot.source_url,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "period_covered": snapshot.period_covered,
        "metric_name": metric.name,
        "metric_unit": metric.unit,
        "total_amount": total_amount,
        "median": metric.typical,
        "high_outlier_threshold": metric.high_outlier_threshold,
        "beyond_statistics_threshold": metric.beyond_statistics_threshold,
        "bottom_band_share": metric.bottom_band_share,
        "low_side_not_checked": (
            "最下位階級が全体の"
            f"{(metric.bottom_band_share or 0) * 100:.1f}%を占め、その内側の分布が"
            "統計に無いため、下振れ(安すぎる方向)は判定していない"
        ),
    }
