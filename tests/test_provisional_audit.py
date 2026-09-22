"""階層2(仮採用+抜き取り監査)の、抜き取り監査の回帰テスト。

`docs/audit_unimplemented_trial_claims.md` 3節で「v8 3-3節は階層2を
『一定割合を事後監査する』と定義しているのに、監査の実装が0行」と報告した
欠落に対応する。

**このファイルの主目的は、監査が「存在するが機能していない」状態を検出する
ことである。** 監査が形式的に走っているのに何も検出していない状態は、
集計表を見ても気づけない(v8 8章の穴2そのもの)。そのため、
「監査が実際に走ったこと」「誤りを実際に捕まえること」「正解が無いときに
的中率を捏造しないこと」を、それぞれ明示的に縛る。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.provisional_audit import (
    DEFAULT_MINIMUM_SAMPLE,
    DEFAULT_SAMPLING_RATE,
    AuditCandidate,
    AuditConfigurationError,
    collect_tier2_population,
    expand_to_categories,
    format_report,
    plan_sample_size,
    run_provisional_audit,
)

FIXED_NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


def _candidates(count: int, *, category: str = "detector") -> list[AuditCandidate]:
    return [
        AuditCandidate(
            target=f"element_{i:02d}", adopted_range=(4, 6), unit="count",
            category=category, axis_id="image", method_id=category,
        )
        for i in range(count)
    ]


def _all_correct(candidates: list[AuditCandidate]) -> dict[str, int]:
    return {c.target: 5 for c in candidates}


# =====================================================================
# 監査が実際に走ったことを示せる(空回りの検出)
# =====================================================================


def test_the_audit_actually_samples_something() -> None:
    """母集団があるのに抽出0件、という空回りが起きないこと。"""
    candidates = _candidates(10)
    report = run_provisional_audit(candidates, _all_correct(candidates), seed=0)

    assert report.sample_size > 0
    assert len(report.findings) == report.sample_size
    assert report.status == "verified"


def test_a_tiny_population_is_still_audited() -> None:
    """母集団が小さくても、切り捨てで0件にならないこと。

    ``int(3 * 0.3) == 0`` で監査が静かに空回りする典型例を塞ぐ。
    """
    for population_size in (1, 2, 3):
        candidates = _candidates(population_size)
        report = run_provisional_audit(candidates, _all_correct(candidates), seed=0)
        assert report.sample_size == population_size, population_size
        assert report.sample_size >= 1


@pytest.mark.parametrize(
    ("population", "expected"),
    [(0, 0), (1, 1), (3, 3), (5, 5), (10, 5), (20, 6), (100, 30), (1000, 300)],
)
def test_sample_size_follows_the_rate_with_a_floor(population: int, expected: int) -> None:
    """抽出件数が「30%、ただし最小5件、母集団が上限」であること。"""
    assert plan_sample_size(population) == expected


def test_a_zero_minimum_is_refused() -> None:
    """最小抽出件数0を設定できないこと。

    0を許すと、母集団があるのに1件も監査しない設定が作れてしまう。
    """
    with pytest.raises(AuditConfigurationError):
        plan_sample_size(10, minimum_sample=0)


def test_the_log_records_that_the_audit_ran() -> None:
    """監査の実行記録が残ること(母集団・抽出件数・シード・対象)。"""
    candidates = _candidates(10)
    report = run_provisional_audit(
        candidates, _all_correct(candidates), seed=7, now=FIXED_NOW
    )

    log = report.log
    assert log.population_size == 10
    assert log.sample_size == 5
    assert log.seed == 7
    assert len(log.sampled_targets) == 5
    assert log.status == "verified"

    line = log.format_line()
    assert "provisional_audit" in line
    assert "population=10" in line
    assert "sample=5" in line
    assert "seed=7" in line
    assert "2026-09-21T12:00:00Z" in line
    # 抽出した対象が記録に出ること(あとから追跡できる)
    for target in log.sampled_targets:
        assert target in line


def test_sampling_is_deterministic_and_varies_by_seed() -> None:
    """同じシードなら再現し、違うシードなら別の対象が選ばれること。

    常に先頭N件を返すような「乱数のふりをした」実装を排除する。
    """
    candidates = _candidates(20)
    truth = _all_correct(candidates)

    first = run_provisional_audit(candidates, truth, seed=3)
    again = run_provisional_audit(candidates, truth, seed=3)
    assert [f.target for f in first.findings] == [f.target for f in again.findings]

    patterns = {
        tuple(f.target for f in run_provisional_audit(candidates, truth, seed=s).findings)
        for s in range(6)
    }
    assert len(patterns) > 1, "シードを変えても同じ対象しか選ばれていない"

    # 先頭N件をそのまま返していないこと
    head = tuple(c.target for c in candidates[: first.sample_size])
    assert any(
        tuple(f.target for f in run_provisional_audit(candidates, truth, seed=s).findings) != head
        for s in range(6)
    )


def test_sampling_does_not_depend_on_input_order() -> None:
    """呼び出し側が渡す順序で結果が変わらないこと。"""
    import random

    candidates = _candidates(20)
    truth = _all_correct(candidates)
    shuffled = candidates[:]
    random.Random(99).shuffle(shuffled)

    a = run_provisional_audit(candidates, truth, seed=3)
    b = run_provisional_audit(shuffled, truth, seed=3)
    assert [f.target for f in a.findings] == [f.target for f in b.findings]


# =====================================================================
# 監査が実際に誤りを捕まえる
# =====================================================================


def test_a_planted_error_is_caught_when_sampled() -> None:
    """採用範囲の外に正解がある要素を、不一致として検出すること。"""
    candidates = _candidates(10)
    truth = _all_correct(candidates)
    report_before = run_provisional_audit(candidates, truth, seed=1)
    sampled_target = report_before.findings[0].target

    # 抽出される対象に誤りを仕込む
    truth[sampled_target] = 99
    report = run_provisional_audit(candidates, truth, seed=1)

    assert report.found_error is True
    assert report.mismatch_count == 1
    assert report.hit_rate == pytest.approx(4 / 5)
    finding = next(f for f in report.findings if f.target == sampled_target)
    assert finding.outcome == "mismatch"
    assert finding.ground_truth == 99
    assert "外に" in finding.note


def test_an_error_inside_the_adopted_range_is_a_match() -> None:
    """採用範囲の中に正解があれば一致と判定すること(境界を含む)。"""
    candidates = _candidates(5)
    for value in (4, 5, 6):  # adopted_range=(4, 6) の下限・中・上限
        report = run_provisional_audit(
            candidates, {c.target: value for c in candidates}, seed=0
        )
        assert report.mismatch_count == 0, value
        assert report.hit_rate == 1.0


def test_an_error_outside_the_population_is_not_reported() -> None:
    """抽出されなかった要素の誤りは、この監査では検出されないこと。

    抜き取り監査の性質そのもの。「監査した=全部正しい」ではないことを、
    テストとしても明示しておく。
    """
    candidates = _candidates(20)
    truth = _all_correct(candidates)
    report = run_provisional_audit(candidates, truth, seed=3)
    not_sampled = {c.target for c in candidates} - {f.target for f in report.findings}
    assert not_sampled, "前提: 抽出されない要素が存在する"

    truth[sorted(not_sampled)[0]] = 99
    after = run_provisional_audit(candidates, truth, seed=3)
    assert after.found_error is False  # 見逃す。これは仕様であって不具合ではない


# =====================================================================
# 正解が無いときに的中率を捏造しない
# =====================================================================


def test_no_ground_truth_never_reports_a_hit_rate() -> None:
    """正解が1件も無いとき、的中率100%ではなく「算出不能」になること。

    **監査が空回りする最大の道がここである。** 正解の無い対象を「一致」と
    数えると、的中率は常に100%になり、誰も異常に気づけない。
    """
    candidates = _candidates(10)
    report = run_provisional_audit(candidates, {}, seed=0)

    assert report.status == "awaiting_human"
    assert report.hit_rate is None, "正解が無いのに的中率が出た"
    assert report.match_count == 0
    assert report.verifiable_count == 0
    assert report.unverifiable_count == report.sample_size
    assert report.found_error is False


def test_partial_ground_truth_only_counts_what_was_verified() -> None:
    """照合できなかった対象は、的中率の分母にも分子にも入らないこと。"""
    candidates = _candidates(10)
    report_plan = run_provisional_audit(candidates, {}, seed=0)
    sampled = [f.target for f in report_plan.findings]

    # 抽出5件のうち2件だけ正解がある(1件一致・1件不一致)
    truth = {sampled[0]: 5, sampled[1]: 99}
    report = run_provisional_audit(candidates, truth, seed=0)

    assert report.verifiable_count == 2
    assert report.match_count == 1
    assert report.mismatch_count == 1
    assert report.unverifiable_count == 3
    assert report.hit_rate == pytest.approx(0.5)


def test_an_empty_population_is_not_a_perfect_score() -> None:
    """階層2の要素が0件の状態を、「監査して全部当たった」と報告しないこと。"""
    report = run_provisional_audit([], {}, seed=0)

    assert report.status == "no_population"
    assert report.hit_rate is None
    assert report.sample_size == 0
    assert report.findings == ()
    assert report.log.status == "no_population"

    text = format_report(report)
    assert "1件も無い" in text
    assert "違う" in text  # 「全部当たった」とは違う、と明記していること


# =====================================================================
# 母集団は階層2だけ
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


def test_only_tier2_elements_enter_the_population() -> None:
    """階層1と階層3が母集団に混ざらないこと。

    混ざると的中率が薄まり、階層2そのものの品質が見えなくなる。
    """
    assessments = _assessments()
    assert assessments["tier1"][0].action == "auto_confirm"
    assert assessments["tier2"][0].action == "provisional_audit"
    assert assessments["tier3"][0].action == "requires_review"

    population = collect_tier2_population(assessments)

    assert [c.target for c in population] == ["tier2"]
    assert population[0].adopted_range == (5, 5)


def test_the_population_carries_what_the_audit_needs() -> None:
    """母集団の各要素が、照合とカテゴリ拡大に必要な情報を持つこと。"""
    population = collect_tier2_population(_assessments())
    candidate = population[0]

    assert candidate.unit == "count"
    assert candidate.axis_id == "image"
    assert candidate.method_id == "method_drawing-A"
    assert candidate.category == "method_drawing-A"


def test_a_tier2_decision_without_a_range_is_refused() -> None:
    """階層2なのに採用範囲が無い入力を、黙って飛ばさないこと。"""

    class _Broken:
        action = "provisional_audit"
        tier = 2
        confirmed_range = None

    with pytest.raises(AuditConfigurationError):
        collect_tier2_population({"x": (_Broken(), [])})  # type: ignore[dict-item]


# =====================================================================
# v8 4-3節ルール3: 同じカテゴリの要素全体へ拡大
# =====================================================================


def test_a_mismatch_expands_the_audit_to_its_whole_category() -> None:
    """誤りが出たカテゴリの要素を、抽出されなかったものも含めて全件返すこと。"""
    good = _candidates(6, category="detector_a")
    suspect = [
        AuditCandidate(target=f"bad_{i}", adopted_range=(4, 6), unit="count",
                       category="detector_b", axis_id="image", method_id="detector_b")
        for i in range(4)
    ]
    candidates = good + suspect
    truth = {c.target: 5 for c in candidates}

    plan = run_provisional_audit(candidates, truth, seed=0)
    sampled_bad = [f.target for f in plan.findings if f.category == "detector_b"]
    assert sampled_bad, "前提: detector_b から少なくとも1件抽出される"

    truth[sampled_bad[0]] = 99
    report = run_provisional_audit(candidates, truth, seed=0)

    assert report.expanded_categories == ("detector_b",)
    expanded = expand_to_categories(candidates, report.expanded_categories)
    # 抽出されなかった detector_b の要素も対象に入る
    assert {c.target for c in expanded} == {c.target for c in suspect}
    assert len(expanded) > len(sampled_bad)


def test_no_mismatch_means_no_expansion() -> None:
    candidates = _candidates(10)
    report = run_provisional_audit(candidates, _all_correct(candidates), seed=0)
    assert report.expanded_categories == ()


# =====================================================================
# 検出力を正直に出す
# =====================================================================


def test_detection_probability_matches_the_hypergeometric_value() -> None:
    """検出確率が手計算と一致すること。

    母集団10件・誤り1件・抽出5件: 1 - C(9,5)/C(10,5) = 1 - 126/252 = 0.5
    """
    candidates = _candidates(10)
    report = run_provisional_audit(candidates, _all_correct(candidates), seed=0)

    assert report.sample_size == 5
    assert report.detection_probability(1) == pytest.approx(0.5)
    assert report.detection_probability(0) == 0.0
    # 全件が誤りなら必ず捕まる
    assert report.detection_probability(10) == pytest.approx(1.0)


def test_the_minimum_sample_measurably_improves_detection() -> None:
    """最小5件を入れた理由(検出力が上がること)を数値で縛る。

    母集団10件では、抽出率30%のみだと3件で検出確率30%。最小5件で50%になる。
    """
    candidates = _candidates(10)
    truth = _all_correct(candidates)

    rate_only = run_provisional_audit(candidates, truth, seed=0, minimum_sample=1)
    with_floor = run_provisional_audit(candidates, truth, seed=0)

    assert rate_only.sample_size == 3
    assert with_floor.sample_size == 5
    assert rate_only.detection_probability(1) == pytest.approx(0.3)
    assert with_floor.detection_probability(1) == pytest.approx(0.5)


def test_full_coverage_detects_everything() -> None:
    """抽出率100%なら見逃しが無いこと。"""
    candidates = _candidates(8)
    report = run_provisional_audit(
        candidates, _all_correct(candidates), seed=0, sampling_rate=1.0
    )
    assert report.sample_size == 8
    assert report.detection_probability(1) == pytest.approx(1.0)


# =====================================================================
# 報告書フォーマット
# =====================================================================


def test_the_report_leads_with_the_error_when_one_was_found() -> None:
    candidates = _candidates(10)
    truth = _all_correct(candidates)
    plan = run_provisional_audit(candidates, truth, seed=1)
    truth[plan.findings[0].target] = 99

    text = format_report(run_provisional_audit(candidates, truth, seed=1))

    assert "誤りを1件検出した" in text
    assert "的中率" in text
    assert "見逃す" in text, "検出力の留保が書かれていない"
    assert "provisional_audit population=10" in text, "実行記録が載っていない"


def test_the_report_refuses_to_claim_success_without_ground_truth() -> None:
    """正解が無いときの報告が、成功と読めないこと。"""
    text = format_report(run_provisional_audit(_candidates(10), {}, seed=0))

    assert "照合できていない" in text
    assert "的中率は出せない" in text
    assert "全件一致" in text  # 「全件一致ではない」と明記していること


def test_a_clean_report_still_states_the_limitation() -> None:
    """全件一致でも「誤りが無い証明ではない」と書くこと。"""
    candidates = _candidates(10)
    text = format_report(run_provisional_audit(candidates, _all_correct(candidates), seed=0))

    assert "すべて一致した" in text
    assert "証明ではない" in text


def test_defaults_are_the_documented_ones() -> None:
    assert DEFAULT_SAMPLING_RATE == 0.30
    assert DEFAULT_MINIMUM_SAMPLE == 5
