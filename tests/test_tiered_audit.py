"""抜き取り監査を階層1にも広げたときの回帰テスト。

これまで抜き取り監査の母集団は階層2(``action == "provisional_audit"``)
だけだった。理由は `arbitration/provisional_audit.py` の
``collect_tier2_population`` の docstring にあるとおり、**階層1や階層3を
混ぜると的中率が薄まり、階層2そのものの品質が見えなくなる**からである。

階層1を母集団に入れると、この前提は次のように変わる。

- **階層ごとに母集団・抽出件数・的中率を分けて数えるかぎり、薄まりは
  起きない。** 薄まるのは1つの的中率に混ぜたときだけである。だから
  このテストは「混ぜられないこと」を明示的に縛る
  (``TieredAuditReport`` に的中率を1つにまとめる入口を作らない)。
- **階層1は ``auto_confirm`` で、人が一度も見ない。** 階層2は少なくとも
  抜き取られれば人が見る。見逃したときの重さが違うので、抽出率も階層ごとに
  別々に決められる必要がある。
- **v8 4-3節ルール3の拡大は階層をまたぐ。** 系統誤差は手法から出るので、
  階層2で見つかった誤りと同じ手法の階層1の要素は、抽出されていなくても
  拡大監査の対象になる。

**既定値は変えていない。** 階層2は今までどおり抽出率30%・最小5件で、
階層1は**既定では監査しない**(母集団に入れるには明示的に指定する)。
階層1の抽出率をいくつにするかは判断が要るので、このリポジトリでは決めない。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.provisional_audit import (
    AUDITABLE_TIERS,
    DEFAULT_MINIMUM_SAMPLE,
    DEFAULT_SAMPLING_RATE,
    DEFAULT_TIER_POLICIES,
    AuditCandidate,
    AuditConfigurationError,
    TierAuditPolicy,
    TieredAuditReport,
    collect_audit_population,
    collect_tier2_population,
    expand_to_categories,
    format_tiered_report,
    run_provisional_audit,
    run_tiered_audit,
)

FIXED_NOW = datetime(2026, 9, 22, 3, 0, 0, tzinfo=timezone.utc)


def _candidates(
    count: int, *, tier: int = 2, category: str = "detector", prefix: str = "e"
) -> list[AuditCandidate]:
    return [
        AuditCandidate(
            target=f"{prefix}_{tier}_{i:02d}", adopted_range=(4, 6), unit="count",
            category=category, axis_id="image", method_id=category, tier=tier,
        )
        for i in range(count)
    ]


def _all_correct(candidates: list[AuditCandidate]) -> dict[str, int]:
    return {c.target: 5 for c in candidates}


def _both_tiers() -> list[AuditCandidate]:
    return _candidates(12, tier=1, category="vector") + _candidates(12, tier=2)


# =====================================================================
# 既定値を変えていないこと
# =====================================================================


def test_tier2_defaults_are_untouched() -> None:
    """階層2の既定の抽出率と最小件数が、今までの値のままであること。"""
    policy = DEFAULT_TIER_POLICIES[2]

    assert policy.sampling_rate == DEFAULT_SAMPLING_RATE == 0.30
    assert policy.minimum_sample == DEFAULT_MINIMUM_SAMPLE == 5


def test_tier1_is_not_audited_by_default() -> None:
    """階層1の既定値をコードに入れていないこと。

    階層1の抽出率をいくつにするかは判断が要る。既定で走らせてしまうと、
    値が決まっていないまま運用に入る。
    """
    assert 1 not in DEFAULT_TIER_POLICIES


def test_the_default_population_is_still_tier2_only() -> None:
    """既定の母集団が階層2だけのままであること。"""
    population = collect_audit_population(_assessments())

    assert [c.target for c in population] == ["tier2"]
    assert population[0].tier == 2


def test_the_tiered_run_reproduces_the_existing_tier2_result() -> None:
    """階層2だけを渡したとき、今までの ``run_provisional_audit`` と同じ結果になること。

    抽出されるのが同じ5件でなければ、階層2の挙動を変えてしまっている。
    """
    candidates = _candidates(17)
    truth = _all_correct(candidates)

    before = run_provisional_audit(candidates, truth, seed=3, now=FIXED_NOW)
    after = run_tiered_audit(candidates, truth, seed=3, now=FIXED_NOW)
    tier2 = after.report_for(2)

    assert tier2 is not None
    assert tier2.sample_size == before.sample_size
    assert [f.target for f in tier2.findings] == [f.target for f in before.findings]
    assert tier2.hit_rate == before.hit_rate


# =====================================================================
# 階層ごとに別々の抽出率を指定できること
# =====================================================================


def test_each_tier_uses_its_own_sampling_rate() -> None:
    """階層1と階層2で、別々の抽出率と最小件数が効くこと。"""
    candidates = _candidates(20, tier=1) + _candidates(20, tier=2)
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={
            1: TierAuditPolicy(sampling_rate=0.50, minimum_sample=5),
            2: TierAuditPolicy(sampling_rate=0.30, minimum_sample=5),
        },
        seed=0, now=FIXED_NOW,
    )

    assert report.report_for(1).sample_size == 10  # 20 * 0.50
    assert report.report_for(2).sample_size == 6  # 20 * 0.30


def test_a_tier_in_the_population_without_a_policy_is_refused() -> None:
    """方針を決めていない階層が母集団に混ざったら、黙って捨てずに止めること。

    黙って捨てると「階層1も監査しているつもり」で1件も監査されない。
    このモジュールが塞いでいる空回りと同じ形である。
    """
    candidates = _both_tiers()

    with pytest.raises(AuditConfigurationError):
        run_tiered_audit(candidates, _all_correct(candidates), seed=0)


def test_a_zero_minimum_is_refused_per_tier() -> None:
    """階層ごとの設定でも、最小抽出件数0を拒否すること。"""
    with pytest.raises(AuditConfigurationError):
        TierAuditPolicy(sampling_rate=0.3, minimum_sample=0)


def test_an_out_of_range_rate_is_refused_per_tier() -> None:
    with pytest.raises(AuditConfigurationError):
        TierAuditPolicy(sampling_rate=0.0, minimum_sample=5)
    with pytest.raises(AuditConfigurationError):
        TierAuditPolicy(sampling_rate=1.5, minimum_sample=5)


def test_a_tier_with_no_elements_is_not_a_perfect_score() -> None:
    """方針はあるが要素が0件の階層を、「監査して全部当たった」にしないこと。"""
    candidates = _candidates(6, tier=2)
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={1: TierAuditPolicy(0.5, 5), 2: TierAuditPolicy(0.3, 5)},
        seed=0, now=FIXED_NOW,
    )

    tier1 = report.report_for(1)
    assert tier1.status == "no_population"
    assert tier1.hit_rate is None


# =====================================================================
# 的中率を階層ごとに分けること(薄めないこと)
# =====================================================================


def test_hit_rates_are_reported_per_tier_and_never_pooled() -> None:
    """階層をまたいで的中率を1つにまとめる入口を作らないこと。

    まとめてしまうと、階層1の大きな母集団が階層2の誤りを薄めて隠す。
    これが元の実装が階層2だけを母集団にしていた理由そのものである。
    """
    candidates = _both_tiers()
    truth = _all_correct(candidates)
    # 階層2の要素を1件だけ外す。
    truth[candidates[-1].target] = 99

    report = run_tiered_audit(
        candidates, truth,
        policies={1: TierAuditPolicy(1.0, 1), 2: TierAuditPolicy(1.0, 1)},
        seed=0, now=FIXED_NOW,
    )

    assert report.report_for(1).hit_rate == 1.0
    assert report.report_for(2).hit_rate == 11 / 12
    assert not hasattr(report, "hit_rate")
    assert report.found_error is True


def test_a_single_tier_report_refuses_mixed_tiers() -> None:
    """階層を混ぜた母集団を ``run_provisional_audit`` に渡せないこと。

    渡せてしまうと、的中率が1つに混ざる経路が残る。
    """
    with pytest.raises(AuditConfigurationError):
        run_provisional_audit(_both_tiers(), {}, seed=0)


# =====================================================================
# 監査の記録に階層が残ること
# =====================================================================


def test_the_log_records_which_tier_was_audited() -> None:
    """実行記録から、どの階層の監査だったかが分かること。"""
    candidates = _both_tiers()
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={1: TierAuditPolicy(0.5, 5), 2: TierAuditPolicy(0.3, 5)},
        seed=0, now=FIXED_NOW,
    )

    line1 = report.report_for(1).log.format_line()
    line2 = report.report_for(2).log.format_line()

    assert "tier=1" in line1
    assert "tier=2" in line2
    assert report.report_for(1).log.tier == 1
    assert report.report_for(2).log.tier == 2


def test_every_finding_carries_its_tier() -> None:
    """1件ごとの監査結果にも階層が残ること。"""
    candidates = _both_tiers()
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={1: TierAuditPolicy(0.5, 5), 2: TierAuditPolicy(0.3, 5)},
        seed=0, now=FIXED_NOW,
    )

    assert all(f.tier == 1 for f in report.report_for(1).findings)
    assert all(f.tier == 2 for f in report.report_for(2).findings)


def test_the_report_text_separates_the_tiers() -> None:
    """報告テキストが階層ごとに分かれ、的中率をまとめて書かないこと。"""
    candidates = _both_tiers()
    truth = _all_correct(candidates)
    truth[candidates[-1].target] = 99

    report = run_tiered_audit(
        candidates, truth,
        policies={1: TierAuditPolicy(1.0, 1), 2: TierAuditPolicy(1.0, 1)},
        seed=0, now=FIXED_NOW,
    )
    text = format_tiered_report(report)

    assert "階層1" in text
    assert "階層2" in text
    assert "tier=1" in text and "tier=2" in text
    # 階層をまたいだ的中率を出していないこと。
    assert "全体の的中率" not in text


# =====================================================================
# 母集団の組み立て
# =====================================================================


def _evidence(target: str, rng: tuple[int, int], source: str, axis: str,
              strength: str = "strong") -> AxisEvidence:
    return AxisEvidence(
        derivation="read",
        target=target, count_range=rng, source_id=source, axis_id=axis,
        method_id=f"method_{source}", unit="count",
        strength=strength, calibrated=True,  # type: ignore[arg-type]
    )


def _assessments() -> dict:
    firewall = AxisQualityFirewall()
    cases = {
        "tier1": [_evidence("tier1", (4, 4), "drawing-A", "image"),
                  _evidence("tier1", (4, 4), "spec-A", "text")],
        "tier2": [_evidence("tier2", (5, 5), "drawing-A", "image"),
                  _evidence("tier2", (4, 6), "db-1", "history", "weak"),
                  _evidence("tier2", (4, 6), "db-2", "rules", "weak")],
        "tier3": [_evidence("tier3", (4, 4), "drawing-A", "image")],
    }
    return {t: (firewall.assess(evs), evs) for t, evs in cases.items()}


def test_tier1_enters_the_population_only_when_asked() -> None:
    """階層1を明示的に指定したときだけ母集団に入り、階層が記録されること。"""
    population = collect_audit_population(_assessments(), tiers=(1, 2))

    assert sorted((c.target, c.tier) for c in population) == [
        ("tier1", 1), ("tier2", 2)
    ]


def test_tier3_is_never_auditable() -> None:
    """階層3を母集団に入れようとしたら止めること。

    階層3は人が必ず見るので抜き取る意味が無く、入れると的中率が薄まる。
    """
    assert AUDITABLE_TIERS == (1, 2)
    with pytest.raises(AuditConfigurationError):
        collect_audit_population(_assessments(), tiers=(2, 3))


def test_a_tier1_decision_without_a_range_is_refused() -> None:
    """階層1なのに採用範囲が無い入力を、黙って飛ばさないこと。"""

    class _Broken:
        action = "auto_confirm"
        tier = 1
        confirmed_range = None

    with pytest.raises(AuditConfigurationError):
        collect_audit_population(
            {"x": (_Broken(), [])}, tiers=(1,)  # type: ignore[dict-item]
        )


def test_a_decision_whose_tier_contradicts_its_action_is_refused() -> None:
    """階層番号と action が食い違う判定を、黙って受け入れないこと。"""

    class _Inconsistent:
        action = "auto_confirm"
        tier = 2
        confirmed_range = (4, 6)

    with pytest.raises(AuditConfigurationError):
        collect_audit_population(
            {"x": (_Inconsistent(), [])}, tiers=(1, 2)  # type: ignore[dict-item]
        )


def test_the_old_collector_still_returns_tier2_only() -> None:
    """既存の ``collect_tier2_population`` の呼び出しが変わらないこと。"""
    population = collect_tier2_population(_assessments())

    assert [c.target for c in population] == ["tier2"]
    assert population[0].tier == 2


# =====================================================================
# v8 4-3節ルール3の拡大は階層をまたぐ
# =====================================================================


def test_expansion_crosses_tiers_for_the_same_method() -> None:
    """階層2で見つかった誤りが、同じ手法の階層1の要素まで拡大対象にすること。

    系統誤差は手法から出るので、同じ手法を使った階層1の要素も疑わしい。
    **返すだけで、自動で階層を下げたりはしない。**
    """
    tier1 = _candidates(4, tier=1, category="shared_method", prefix="a")
    tier2 = _candidates(4, tier=2, category="shared_method", prefix="b")
    candidates = tier1 + tier2
    truth = _all_correct(candidates)
    truth[tier2[0].target] = 99

    report = run_tiered_audit(
        candidates, truth,
        policies={1: TierAuditPolicy(1.0, 1), 2: TierAuditPolicy(1.0, 1)},
        seed=0, now=FIXED_NOW,
    )

    assert report.expanded_categories == ("shared_method",)

    expanded = expand_to_categories(candidates, report.expanded_categories)
    assert len(expanded) == 8
    assert {c.tier for c in expanded} == {1, 2}


def test_no_mismatch_means_no_expansion_across_tiers() -> None:
    candidates = _both_tiers()
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={1: TierAuditPolicy(1.0, 1), 2: TierAuditPolicy(1.0, 1)},
        seed=0, now=FIXED_NOW,
    )

    assert report.expanded_categories == ()
    assert report.found_error is False


# =====================================================================
# 検出力
# =====================================================================


def test_detection_power_is_reported_per_tier() -> None:
    """階層ごとに、その階層の母集団と抽出件数で検出力が出ること。

    階層をまたいだ母集団で計算すると、実際より高い検出力を報告してしまう。
    """
    candidates = _candidates(10, tier=1) + _candidates(10, tier=2)
    report = run_tiered_audit(
        candidates, _all_correct(candidates),
        policies={1: TierAuditPolicy(1.0, 1), 2: TierAuditPolicy(0.3, 5)},
        seed=0, now=FIXED_NOW,
    )

    assert report.report_for(1).detection_probability(1) == 1.0
    assert report.report_for(2).detection_probability(1) == pytest.approx(0.5)
