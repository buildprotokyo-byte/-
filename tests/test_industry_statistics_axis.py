"""合計金額サニティチェック(出口検査)のテスト。

このファイルは以前、業界一般統計軸を「対象要素の数量レンジを出す弱い軸」
として検証していた。実データを入れた結果その位置づけが成り立たないことが
分かり(`docs/design_v8.md` 5-2節)、出口検査に位置づけ直したため、
テストも書き換えている。

ここで縛っている核心は3つ。

1. **金額を数量レンジとして扱う経路が存在しないこと**(削除したことの回帰)
2. **この検査は確信度階層を下げることしかできないこと**(上げる経路が無い)
3. **下振れ・新築・単位違いでは棄権すること**(推測で埋めない)
"""

from __future__ import annotations

import importlib
import inspect
from datetime import date

import pytest

from arbitration.total_amount_sanity_check import (
    EXPECTED_UNIT,
    TotalAmountVerdict,
    check_total_amount,
)
from axes.industry_statistics_axis.metric_names import (
    available_uses,
    metric_name_for_use,
)
from axes.industry_statistics_axis.snapshot import (
    IndustryMetric,
    StatisticsSnapshot,
    load_latest_snapshot,
)

HOUSING = "個別工事の受注額:住宅:住宅 計"


@pytest.fixture(scope="module")
def shipped() -> StatisticsSnapshot:
    snapshot = load_latest_snapshot()
    assert snapshot is not None, "同梱のスナップショットが読めない"
    return snapshot


def _snapshot(**metric_kwargs) -> StatisticsSnapshot:
    """検査用の最小スナップショット。既定は同梱の実データと同じ形にする。"""
    defaults = dict(
        name=HOUSING,
        unit="万円/件",
        low=3.2,
        high=245.4,
        typical=31.7,
        high_outlier_threshold=796.7,
        beyond_statistics_threshold=3000.0,
        bottom_band_share=0.7878,
    )
    defaults.update(metric_kwargs)
    return StatisticsSnapshot(
        source_name="国土交通省 建築物リフォーム・リニューアル調査(テスト用)",
        source_url="https://www.e-stat.go.jp/",
        fetched_at=date(2026, 9, 21),
        period_covered="令和7年度計",
        metrics={defaults["name"]: IndustryMetric(**defaults)},
    )


# --- 1. 金額を数量として扱う経路が消えていること ---------------------------


def test_the_quantity_range_module_is_gone() -> None:
    """`reading.py`(金額を count_range に入れていたモジュール)は削除済み。

    これが「3〜246万円が建具4本を支持した」という誤判定の入口だったため、
    復活したら落ちるようにしておく。
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("axes.industry_statistics_axis.reading")


def test_statistical_axis_is_not_allowed_in_axis_arbitration() -> None:
    """軸間照合の許可リストから "statistical" が外れていること。"""
    from arbitration.inference_orchestrator import ALLOWED_AXES

    assert "statistical" not in ALLOWED_AXES


def test_no_public_function_produces_an_axis_evidence_from_the_statistics() -> None:
    """統計パッケージから ``AxisEvidence`` を作る公開関数が無いこと。"""
    from axes.industry_statistics_axis import fetch, metric_names, snapshot

    for module in (fetch, metric_names, snapshot):
        source = inspect.getsource(module)
        assert "AxisEvidence" not in source, f"{module.__name__} が AxisEvidence に触れている"


# --- 2. 階層を下げることしかできないこと -----------------------------------


def test_verdict_cannot_claim_support_without_forcing_review() -> None:
    """``forces_review`` と ``status`` の対応が型レベルで強制されること。"""
    with pytest.raises(ValueError):
        TotalAmountVerdict(status="within_distribution", forces_review=True, reason="x")
    with pytest.raises(ValueError):
        TotalAmountVerdict(status="high_outlier", forces_review=False, reason="x")


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_within_distribution_never_raises_the_confidence_tier(tier: int) -> None:
    """分布の中に入っていても、階層は一切上がらない(5-2節の非対称性)。"""
    verdict = check_total_amount(_snapshot(), 50.0, metric_name=HOUSING)
    assert verdict.status == "within_distribution"
    assert verdict.forces_review is False
    assert verdict.applied_tier(tier) == tier


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_high_outlier_forces_tier3_from_any_tier(tier: int) -> None:
    """第99パーセンタイル超は、どの階層からでも階層3へ落とす。"""
    verdict = check_total_amount(_snapshot(), 900.0, metric_name=HOUSING)
    assert verdict.status == "high_outlier"
    assert verdict.forces_review is True
    assert verdict.applied_tier(tier) == 3


def test_beyond_statistics_is_reported_separately_from_high_outlier() -> None:
    """上限の無い最上位階級に落ちた場合は、別のステータスで区別する。"""
    verdict = check_total_amount(_snapshot(), 5000.0, metric_name=HOUSING)
    assert verdict.status == "beyond_statistics"
    assert verdict.forces_review is True
    assert "これ以上何も言えない" in verdict.reason


def test_applied_tier_never_returns_a_tier_better_than_given() -> None:
    """``applied_tier`` は入力より良い(小さい)階層を返さない。"""
    for amount in (0.0, 31.7, 245.4, 796.7, 800.0, 3000.0, 999_999.0):
        verdict = check_total_amount(_snapshot(), amount, metric_name=HOUSING)
        for tier in (1, 2, 3):
            assert verdict.applied_tier(tier) >= tier


# --- 3. 判定材料が無ければ棄権すること -------------------------------------


def test_the_low_side_is_never_flagged() -> None:
    """下振れ(安すぎる方向)は、どれだけ小さくても指摘しない。

    最下位階級が全体の 59〜82% を占め、その内側の分布が統計に無いため、
    第5パーセンタイルは階級内一様分布の仮定の産物にすぎない。
    """
    for amount in (0.0, 0.01, 0.1, 1.0, 3.19):
        verdict = check_total_amount(_snapshot(), amount, metric_name=HOUSING)
        assert verdict.forces_review is False, f"{amount} が下振れとして指摘された"
        assert verdict.status == "within_distribution"


def test_new_construction_is_out_of_scope() -> None:
    """新築には適用できない(統計がリフォーム・リニューアルの受注額のため)。"""
    verdict = check_total_amount(
        _snapshot(), 900.0, metric_name=HOUSING, work_kind="new_construction"
    )
    assert verdict.status == "abstained"
    assert verdict.forces_review is False


def test_a_mismatched_unit_abstains_instead_of_reinterpreting() -> None:
    """単位が違うときは、読み替えずに棄権する。今回の修正の発端そのもの。"""
    verdict = check_total_amount(_snapshot(), 900.0, metric_name=HOUSING, unit="円")
    assert verdict.status == "abstained"
    assert "単位" in verdict.reason


def test_an_unknown_use_abstains() -> None:
    verdict = check_total_amount(_snapshot(), 900.0, metric_name="存在しない用途")
    assert verdict.status == "abstained"


def test_a_placeholder_snapshot_abstains() -> None:
    snapshot = StatisticsSnapshot(
        source_name="x", source_url="y", fetched_at=date(2026, 1, 1),
        period_covered="z", metrics={}, is_placeholder=True,
    )
    verdict = check_total_amount(snapshot, 900.0, metric_name=HOUSING)
    assert verdict.status == "abstained"


def test_a_use_without_an_upper_threshold_abstains() -> None:
    """分位点が上限の無い階級に落ちる用途は、閾値を捏造せず棄権する。"""
    snapshot = _snapshot(high_outlier_threshold=None, beyond_statistics_threshold=None)
    verdict = check_total_amount(snapshot, 999_999.0, metric_name=HOUSING)
    assert verdict.status == "abstained"


def test_a_negative_total_abstains() -> None:
    verdict = check_total_amount(_snapshot(), -1.0, metric_name=HOUSING)
    assert verdict.status == "abstained"


# --- 同梱の実データで動くこと ----------------------------------------------


def test_the_shipped_snapshot_carries_real_thresholds(shipped: StatisticsSnapshot) -> None:
    """同梱スナップショットの閾値が、原本から計算された実データであること。"""
    assert not shipped.is_placeholder
    metric = shipped.metrics[HOUSING]
    assert metric.unit == f"{EXPECTED_UNIT}/件"
    assert metric.high_outlier_threshold == 796.7
    assert metric.beyond_statistics_threshold == 3000.0
    # 上振れの閾値は中央値の10倍よりずっと遠い(「桁違い=10倍」は閾値にならない)
    assert metric.high_outlier_threshold > metric.typical * 10


def test_every_shipped_use_has_an_upper_threshold_or_abstains(
    shipped: StatisticsSnapshot,
) -> None:
    """全用途について、閾値が無ければ棄権する(捏造しない)ことを確かめる。"""
    for name, metric in shipped.metrics.items():
        verdict = check_total_amount(shipped, 999_999.0, metric_name=name)
        if metric.beyond_statistics_threshold is None and metric.high_outlier_threshold is None:
            assert verdict.status == "abstained"
        else:
            assert verdict.forces_review is True


def test_metric_name_lookup_matches_the_shipped_keys(shipped: StatisticsSnapshot) -> None:
    assert metric_name_for_use("住宅", "住宅 計") == HOUSING
    assert HOUSING in available_uses(shipped)
    with pytest.raises(ValueError):
        metric_name_for_use("存在しない分類", "住宅 計")
