"""出口検査が、軸間照合の「後ろ」に付くことの統合テスト。

このファイルは以前、業界一般統計軸が過去実績軸を補完して階層3→階層2へ
昇格させることを検証していた。**その結論は撤回された。** 検証が架空項目
``door_count``(単位 箇所/物件)に依存しており、実データには金額の分布しか
無いため再現しない(`docs/industry_statistics_axis_report.md` 4節、
`docs/design_v8.md` 5-2節)。

いま縛るのは逆向きの性質である。

* 統計は軸間照合に**参加できない**(昇格に寄与する経路が無い)
* 出口検査は、ファイアウォールが出した階層を**下げることしかできない**
"""

from __future__ import annotations

from datetime import date

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.total_amount_sanity_check import check_total_amount
from axes.industry_statistics_axis.snapshot import StatisticsSnapshot, load_latest_snapshot

TARGET = "door_count"
HOUSING = "個別工事の受注額:住宅:住宅 計"


@pytest.fixture(scope="module")
def shipped() -> StatisticsSnapshot:
    snapshot = load_latest_snapshot()
    assert snapshot is not None
    return snapshot


def _history_axis_evidence(count_range: tuple[int, int]) -> AxisEvidence:
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
    """強い軸1つ+過去実績軸だけでは階層3のまま(ここは以前から変わらない)。"""
    decision = AxisQualityFirewall().assess([
        _strong_axis_evidence((4, 4)),
        _history_axis_evidence((3, 5)),
    ])
    assert decision.tier == 3
    assert decision.action == "requires_review"


def test_the_retracted_tier2_promotion_has_no_code_path_left(
    shipped: StatisticsSnapshot,
) -> None:
    """撤回された「統計軸を足して階層2へ」を実行する手段が残っていないこと。

    以前は ``axis_evidence_for_metric()`` が金額のレンジを ``count_range``
    に入れて渡せたため、「3〜246万円」が「建具4本」を支持したと判定されて
    階層2へ昇格した。その関数を削除したので、統計から ``AxisEvidence`` を
    作る公開経路は存在しない。
    """
    import axes.industry_statistics_axis as package

    assert not hasattr(package, "reading")
    for name in ("axis_evidence_for_metric", "reading_for_metric"):
        assert not hasattr(package, name)

    # 実データには対象要素の数量に相当する項目がそもそも無い。
    assert TARGET not in shipped.metrics
    assert all(metric.unit == "万円/件" for metric in shipped.metrics.values())


def test_the_exit_check_can_only_lower_the_tier_decided_by_the_firewall(
    shipped: StatisticsSnapshot,
) -> None:
    """ファイアウォールが階層1を出しても、出口検査は下げるだけで上げない。"""
    firewall = AxisQualityFirewall()
    decision = firewall.assess([
        _strong_axis_evidence((4, 4)),
        AxisEvidence(
            target=TARGET, count_range=(4, 4), source_id="spec-sheet",
            axis_id="text", method_id="spec_parser", calibrated=True,
        ),
    ])
    assert decision.tier == 1  # 独立した強い軸2つの一致

    # 分布の中に収まる合計金額 → 階層1がそのまま維持される(上がりも下がりもしない)
    inside = check_total_amount(shipped, 50.0, metric_name=HOUSING)
    assert inside.forces_review is False
    assert inside.applied_tier(decision.tier) == 1

    # 桁違いの合計金額 → 階層1でも階層3へ落ちる
    outside = check_total_amount(shipped, 90_000.0, metric_name=HOUSING)
    assert outside.forces_review is True
    assert outside.applied_tier(decision.tier) == 3


def test_the_exit_check_does_not_change_the_confirmed_range(
    shipped: StatisticsSnapshot,
) -> None:
    """出口検査は数量そのものには触れない(確定レンジを書き換えない)。"""
    decision = AxisQualityFirewall().assess([_strong_axis_evidence((4, 4))])
    before = decision.confirmed_range
    verdict = check_total_amount(shipped, 90_000.0, metric_name=HOUSING)
    assert verdict.forces_review is True
    assert decision.confirmed_range == before == (4, 4)


def test_the_exit_check_abstains_when_the_statistics_cannot_speak() -> None:
    """スナップショットが無い・プレースホルダーのときは棄権する。"""
    empty = StatisticsSnapshot(
        source_name="x", source_url="y", fetched_at=date(2026, 1, 1),
        period_covered="z", metrics={}, is_placeholder=True,
    )
    verdict = check_total_amount(empty, 90_000.0, metric_name=HOUSING)
    assert verdict.status == "abstained"
    assert verdict.applied_tier(1) == 1  # 棄権は階層を動かさない
