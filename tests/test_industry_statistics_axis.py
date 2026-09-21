"""業界一般統計軸(axes/industry_statistics_axis/)の単体テスト。

段階1(スナップショット・SymbolCountReading互換の出力)と、段階2
(軸品質ファイアウォールへの「補助専用」としての参加制限)の両方を
このファイルでカバーする。
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from axes.industry_statistics_axis.reading import (
    AXIS_ID,
    axis_evidence_for_metric,
    reading_for_metric,
)
from axes.industry_statistics_axis.snapshot import (
    DEFAULT_SNAPSHOT_DIR,
    IndustryMetric,
    StatisticsSnapshot,
    load_latest_snapshot,
    load_snapshot,
    save_snapshot,
)


def _sample_snapshot(*, is_placeholder: bool = False, fetched_at: date | None = None) -> StatisticsSnapshot:
    return StatisticsSnapshot(
        source_name="国土交通省 建築物リフォーム・リニューアル調査(検証用サンプル)",
        source_url="https://www.e-stat.go.jp/",
        fetched_at=fetched_at or date(2026, 1, 1),
        period_covered="2025年度第4四半期(検証用の架空値)",
        metrics={
            "reform_project_count": IndustryMetric(
                name="reform_project_count",
                unit="件/月",
                low=100.0,
                high=200.0,
                typical=150.0,
                sample_description="建設業許可業者5,000者への調査(検証用に丸めた架空値)",
            ),
        },
        is_placeholder=is_placeholder,
    )


# ---------------------------------------------------------------------------
# 段階1: スナップショットの保存・読み込み
# ---------------------------------------------------------------------------


def test_industry_metric_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        IndustryMetric(name="x", unit="件", low=10, high=5)


def test_snapshot_round_trips_through_json(tmp_path) -> None:
    snapshot = _sample_snapshot()
    path = save_snapshot(snapshot, directory=tmp_path)
    assert path.exists()

    loaded = load_snapshot(path)
    assert loaded.source_name == snapshot.source_name
    assert loaded.fetched_at == snapshot.fetched_at
    assert loaded.metrics["reform_project_count"].low == 100.0
    assert loaded.is_placeholder is False


def test_load_latest_snapshot_picks_the_newest_fetched_at(tmp_path) -> None:
    old = _sample_snapshot(fetched_at=date(2026, 1, 1))
    new = _sample_snapshot(fetched_at=date(2026, 4, 1))
    save_snapshot(old, directory=tmp_path)
    save_snapshot(new, directory=tmp_path)

    latest = load_latest_snapshot(tmp_path)
    assert latest is not None
    assert latest.fetched_at == date(2026, 4, 1)


def test_load_latest_snapshot_returns_none_for_empty_directory(tmp_path) -> None:
    assert load_latest_snapshot(tmp_path) is None


def test_shipped_snapshot_is_real_data_not_a_placeholder() -> None:
    """リポジトリに同梱している既定スナップショットが、e-Statから取得した
    実データであり、プレースホルダーではないことを確認する。
    """
    snapshot = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    assert snapshot is not None
    assert snapshot.is_placeholder is False
    assert snapshot.metrics != {}
    assert "e-stat.go.jp" in snapshot.source_url
    assert "建築物リフォーム・リニューアル調査" in snapshot.source_name


def test_shipped_snapshot_metrics_are_well_formed() -> None:
    """同梱スナップショットの全項目が、単位付きで low <= typical <= high に
    なっていることを確認する(取り込み時に順序が壊れていないこと)。
    """
    snapshot = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    assert snapshot is not None
    for name, metric in snapshot.metrics.items():
        assert metric.unit, name
        assert metric.low <= metric.high, name
        assert metric.typical is not None, name
        assert metric.low <= metric.typical <= metric.high, name
        assert metric.sample_description, name


def test_load_latest_snapshot_prefers_real_data_over_a_same_day_placeholder(tmp_path) -> None:
    """同じ取得日に実データとプレースホルダーが並んでいたら、実データを返す。"""
    save_snapshot(_sample_snapshot(is_placeholder=True, fetched_at=date(2026, 5, 1)), tmp_path)
    save_snapshot(_sample_snapshot(fetched_at=date(2026, 5, 1)), tmp_path)

    latest = load_latest_snapshot(tmp_path)

    assert latest is not None
    assert latest.is_placeholder is False


# ---------------------------------------------------------------------------
# 段階1: SymbolCountReading互換の出力
# ---------------------------------------------------------------------------


def test_reading_for_existing_metric_uses_the_metric_range() -> None:
    snapshot = _sample_snapshot()
    reading = reading_for_metric(snapshot, "reform_project_count")

    assert reading.status == "low_confidence"
    assert reading.count_range == (100, 200)
    assert reading.evidence["source_name"] == snapshot.source_name
    assert reading.evidence["fetched_at"] == "2026-01-01"


def test_reading_for_missing_metric_abstains_without_fabricating_numbers() -> None:
    """スナップショットに無い項目の読み取りを試みると、架空の数値を作らず棄権する
    (実データを入れた後も、建具の個数のような未収録の項目は棄権のままになる)。"""
    snapshot = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    reading = reading_for_metric(snapshot, "door_count")

    assert reading.status == "abstained"
    assert reading.evidence["reason"] == "metric_not_available_in_snapshot"


def test_quantity_scale_converts_continuous_statistic_to_integer_range() -> None:
    snapshot = _sample_snapshot()
    reading = reading_for_metric(snapshot, "reform_project_count", quantity_scale=0.5)
    # low=100*0.5=50.0 (floor), high=200*0.5=100.0 (ceil)
    assert reading.count_range == (50, 100)


# ---------------------------------------------------------------------------
# 段階2: 軸品質ファイアウォールへの統合(恒久的に補助専用であることの保証)
# ---------------------------------------------------------------------------


def test_axis_evidence_is_always_weak_and_uncalibrated() -> None:
    snapshot = _sample_snapshot()
    evidence = axis_evidence_for_metric(snapshot, "reform_project_count", target="door_count")

    assert evidence.strength == "weak"
    assert evidence.calibrated is False
    assert evidence.status != "confident"
    assert evidence.axis_id == AXIS_ID
    assert evidence.is_hard_eligible is False


def test_axis_evidence_for_unknown_metric_is_abstained_and_not_hard_eligible() -> None:
    snapshot = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    evidence = axis_evidence_for_metric(snapshot, "door_count", target="door_count")

    assert evidence.status == "abstained"
    assert evidence.is_hard_eligible is False


def test_axis_evidence_from_real_shipped_metric_is_still_only_advisory() -> None:
    """実データが入っても、この軸は弱いまま(強い軸に昇格しない)ことを確認する。"""
    snapshot = load_latest_snapshot(DEFAULT_SNAPSHOT_DIR)
    assert snapshot is not None
    name = next(iter(snapshot.metrics))

    evidence = axis_evidence_for_metric(snapshot, name, target="x")

    assert evidence.status == "low_confidence"
    assert evidence.strength == "weak"
    assert evidence.calibrated is False
    assert evidence.is_hard_eligible is False


def test_forced_fields_cannot_be_overridden_by_keyword_arguments() -> None:
    """axis_evidence_for_metricのシグネチャにstrength/calibratedを渡す経路が
    そもそも存在しないことを確認する(誤って強い軸として登録する経路を
    作らないための設計)。
    """
    import inspect

    signature = inspect.signature(axis_evidence_for_metric)
    assert "strength" not in signature.parameters
    assert "calibrated" not in signature.parameters


def test_randomized_metrics_never_become_hard_eligible() -> None:
    """どんな統計値(範囲・単位)を入れても、is_hard_eligibleが常にFalseに
    なることを、乱数で200回確認する(トリップワイヤーの拡張版)。
    """
    rng = random.Random(20260921)
    for _ in range(200):
        low = rng.uniform(0, 1000)
        high = low + rng.uniform(0, 1000)
        snapshot = StatisticsSnapshot(
            source_name="random-test",
            source_url="https://example.invalid/",
            fetched_at=date(2026, 1, 1),
            period_covered="random",
            metrics={
                "m": IndustryMetric(name="m", unit="件", low=low, high=high, typical=(low + high) / 2)
            },
        )
        evidence = axis_evidence_for_metric(snapshot, "m", target="x")
        assert evidence.is_hard_eligible is False, (low, high)


def test_axis_quality_firewall_never_promotes_industry_statistics_to_a_hard_source() -> None:
    """AxisQualityFirewallに直接通しても、この軸だけではtier1にならず、
    強い軸として積集合の計算に参加しないことを確認する。
    """
    from arbitration.axis_quality_firewall import AxisQualityFirewall

    snapshot = _sample_snapshot()
    evidence = axis_evidence_for_metric(snapshot, "reform_project_count", target="x")

    decision = AxisQualityFirewall().assess([evidence])

    assert decision.tier == 3
    assert decision.action == "requires_review"
    assert decision.confirmed_range is None
    assert evidence.evidence_id in decision.advisory_evidence_ids
    assert evidence.evidence_id not in decision.hard_evidence_ids
