"""段階3: 過去実績軸(自社データ、サンプル不足)と業界一般統計軸(サンプルは
豊富だが自社固有ではない)を、同じ要素に対して並べて出力し、両者がどう
補完し合うかを確認する。

過去実績軸には専用モジュールが存在しない(既存コードでも
``AxisEvidence(axis_id="history", ...)`` を直接組み立てる運用のため、
ここでも同じ流儀で表現する)。
"""

from __future__ import annotations

from datetime import date

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from axes.industry_statistics_axis.reading import axis_evidence_for_metric
from axes.industry_statistics_axis.snapshot import IndustryMetric, StatisticsSnapshot

TARGET = "door_count"


def _sample_snapshot() -> StatisticsSnapshot:
    """検証用の架空スナップショット(実データではない。1節参照)。"""
    return StatisticsSnapshot(
        source_name="国土交通省 建築物リフォーム・リニューアル調査(検証用サンプル)",
        source_url="https://www.e-stat.go.jp/",
        fetched_at=date(2026, 1, 1),
        period_covered="2025年度第4四半期(検証用の架空値)",
        metrics={
            "door_count": IndustryMetric(
                name="door_count", unit="箇所/物件", low=3, high=6, typical=4.5,
                sample_description="建設業許可業者5,000者への調査(検証用に丸めた架空値)",
            )
        },
    )


def _history_axis_evidence(count_range: tuple[int, int]) -> AxisEvidence:
    """過去実績軸(自社データ)の読み取り。専用モジュールが無いため既存の
    流儀(axis_id="history")で直接組み立てる。"""
    return AxisEvidence(
        target=TARGET, count_range=count_range, source_id="自社実績DB",
        axis_id="history", method_id="past_projects", strength="weak", calibrated=True,
    )


def _strong_axis_evidence(count_range: tuple[int, int]) -> AxisEvidence:
    return AxisEvidence(
        target=TARGET, count_range=count_range, source_id="drawing-A",
        axis_id="image", method_id="room_detector", calibrated=True,
    )


def test_history_axis_alone_is_not_enough_for_tier2() -> None:
    """強い軸1つ+過去実績軸(弱い、1つ)だけでは、階層2の条件
    (「複数の」弱い軸の支持)を満たさず、階層3のままになる。"""
    firewall = AxisQualityFirewall()
    decision = firewall.assess([
        _strong_axis_evidence((4, 4)),
        _history_axis_evidence((3, 5)),
    ])

    assert decision.tier == 3
    assert decision.action == "requires_review"


def test_industry_statistics_axis_complements_history_axis_to_reach_tier2() -> None:
    """業界一般統計軸を追加すると、独立した弱い軸が2つになり、階層2
    (仮採用+抜き取り監査)に到達する。これが「両者がどう補完し合うか」の
    具体的な答え: 過去実績軸(自社)と業界一般統計軸(業界全体)は、
    大元のデータ源が別であるため独立した弱い軸として数えられ、
    2つ揃って初めて階層2の条件を満たす。
    """
    snapshot = _sample_snapshot()
    firewall = AxisQualityFirewall()

    decision = firewall.assess([
        _strong_axis_evidence((4, 4)),
        _history_axis_evidence((3, 5)),
        axis_evidence_for_metric(snapshot, "door_count", target=TARGET, source_id="mlit-survey"),
    ])

    assert decision.tier == 2
    assert decision.action == "provisional_audit"
    assert decision.independent_advisory_source_count == 2


def test_weak_axes_never_change_the_confirmed_range_itself() -> None:
    """過去実績軸・業界一般統計軸がいくら支持しても、確定するレンジの値
    そのものは強い軸(1つ)の値のまま変わらない(候補を排除する権限を
    持たない、というv8の方針がそのまま反映されている)。
    """
    snapshot = _sample_snapshot()
    firewall = AxisQualityFirewall()

    without_weak = firewall.assess([_strong_axis_evidence((4, 4))])
    with_weak = firewall.assess([
        _strong_axis_evidence((4, 4)),
        _history_axis_evidence((3, 5)),
        axis_evidence_for_metric(snapshot, "door_count", target=TARGET, source_id="mlit-survey"),
    ])

    assert without_weak.confirmed_range == with_weak.confirmed_range == (4, 4)
    # tierとactionだけが変わる(仮採用+抜き取り監査の対象になる)。
    assert without_weak.tier == 3
    assert with_weak.tier == 2


def test_industry_statistics_axis_disagreeing_with_history_axis_does_not_reach_tier2() -> None:
    """2つの弱い軸が支持し合っていない(候補範囲が重ならない)場合は、
    業界統計軸を追加しても階層2には到達しない。「補完」は無条件の
    足し算ではなく、方向が揃って初めて効くことを確認する。
    """
    snapshot = _sample_snapshot()  # door_count: (3, 6)
    firewall = AxisQualityFirewall()

    decision = firewall.assess([
        _strong_axis_evidence((10, 10)),  # 強い軸の確定値が弱い軸の範囲と大きく乖離
        _history_axis_evidence((3, 5)),
        axis_evidence_for_metric(snapshot, "door_count", target=TARGET, source_id="mlit-survey"),
    ])

    assert decision.tier == 3
    assert decision.confirmed_range == (10, 10)  # 強い軸の値は変わらない
