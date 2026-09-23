"""抜き取りと照合を分けたことの回帰テスト。

なぜ分けるのか
--------------
本番の入口(`intake/drawing_intake.read_drawing()`)は、docstring で
「正解データ(見積明細・ゴールデン)はここでは一切読まない」と約束している。
抜き取りをその入口に差し込むとき、抽出と照合が1つの関数のままだと、
**入口に正解の引き口を渡す経路ができてしまう。**

そこで
``plan_tiered_audit()``(抽出だけ。正解に触らない) と
``score_audit_plan()``(照合だけ) に割り、``run_tiered_audit()`` は
その2つの合成として残す。
`benchmarks/run_golden_eval.py` の `extract_from_drawing()` が PDF しか
引数に取らないのと同じ形である。

正解が引けない間の振る舞い
--------------------------
正解の記入者はまだ決まっていない。既定の引き口 ``NoGroundTruth`` は
**常に ``None`` を返す。** そのとき

- ``hit_rate`` は ``None``(1.0 を返さない)
- ``status`` は ``awaiting_human``(母集団0件とも「全部当たった」とも違う)
- **監査の出力は的中率ではなく、人が確かめるべき対象の一覧になる**

一覧には根拠(ページ番号・座標・元の文字列)が付く。付けないと、人が
一覧を見ても何を確かめればいいのか分からない。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from arbitration.provisional_audit import (
    AuditCandidate,
    AuditConfigurationError,
    NoGroundTruth,
    TierAuditPolicy,
    TieredAuditPlan,
    plan_tiered_audit,
    run_provisional_audit,
    run_tiered_audit,
    score_audit_plan,
)

FIXED_NOW = datetime(2026, 9, 22, 6, 0, 0, tzinfo=timezone.utc)

BOTH_TIERS = {1: TierAuditPolicy(0.10, 3), 2: TierAuditPolicy(0.30, 5)}


def _candidates(
    count: int, *, tier: int = 2, category: str = "detector", with_provenance: bool = False
) -> list[AuditCandidate]:
    return [
        AuditCandidate(
            target=f"t{tier}_{i:02d}", adopted_range=(4, 6), unit="count",
            category=category, axis_id="image", method_id=category, tier=tier,
            provenance=(
                {"page_number": i + 1, "text": f"読んだ文字列{i}"}
                if with_provenance else {}
            ),
        )
        for i in range(count)
    ]


def _all_correct(candidates: list[AuditCandidate]) -> dict[str, int]:
    return {c.target: 5 for c in candidates}


# =====================================================================
# 抽出は正解に触らない
# =====================================================================


def test_planning_takes_no_ground_truth_at_all() -> None:
    """抽出の関数が正解を引数に取らないこと。

    取れてしまうと、入口から正解を渡す経路がそこにできる。
    """
    import inspect

    parameters = set(inspect.signature(plan_tiered_audit).parameters)

    assert "ground_truth" not in parameters
    assert not any("truth" in name or "golden" in name for name in parameters)


def test_a_plan_says_what_was_sampled_without_saying_whether_it_was_right() -> None:
    """抽出の結果に、正解や一致・不一致が入っていないこと。"""
    candidates = _candidates(20)
    plan = plan_tiered_audit(
        candidates, policies=BOTH_TIERS, seed=0, now=FIXED_NOW
    )
    tier2 = plan.plan_for(2)

    assert tier2 is not None
    assert tier2.sample_size == 6
    assert len(tier2.sampled) == 6
    assert all(isinstance(item, AuditCandidate) for item in tier2.sampled)
    assert not hasattr(tier2, "hit_rate")
    assert not hasattr(tier2, "findings")


def test_the_sample_carries_the_grounds_a_person_needs() -> None:
    """抜き取った1件に根拠が付いていること。

    対象名だけ渡されても、人は何を確かめればいいのか分からない。
    """
    candidates = _candidates(10, with_provenance=True)
    plan = plan_tiered_audit(candidates, policies=BOTH_TIERS, seed=0, now=FIXED_NOW)

    for item in plan.plan_for(2).sampled:
        assert item.provenance["page_number"] >= 1
        assert item.provenance["text"]


# =====================================================================
# 合成しても今までと同じ結果になる
# =====================================================================


def test_planning_then_scoring_equals_running_it_in_one_go() -> None:
    """割っても ``run_tiered_audit()`` の結果が変わらないこと。"""
    candidates = _candidates(17) + _candidates(11, tier=1)
    truth = _all_correct(candidates)

    one_go = run_tiered_audit(
        candidates, truth, policies=BOTH_TIERS, seed=5, now=FIXED_NOW
    )
    split = score_audit_plan(
        plan_tiered_audit(candidates, policies=BOTH_TIERS, seed=5, now=FIXED_NOW),
        truth,
    )

    for tier in (1, 2):
        assert [f.target for f in split.report_for(tier).findings] == [
            f.target for f in one_go.report_for(tier).findings
        ]
        assert split.report_for(tier).hit_rate == one_go.report_for(tier).hit_rate
        assert split.report_for(tier).log.format_line() == (
            one_go.report_for(tier).log.format_line()
        )


def test_the_single_tier_entry_point_is_unchanged() -> None:
    """階層2だけの既存の呼び出しが、1件も変わらないこと。"""
    candidates = _candidates(17)
    truth = _all_correct(candidates)

    before = run_provisional_audit(candidates, truth, seed=3, now=FIXED_NOW)
    after = score_audit_plan(
        plan_tiered_audit(
            candidates, policies={2: TierAuditPolicy()}, seed=3, now=FIXED_NOW
        ),
        truth,
    ).report_for(2)

    assert [f.target for f in after.findings] == [f.target for f in before.findings]
    assert after.sample_size == before.sample_size


# =====================================================================
# 正解が引けない間
# =====================================================================


def test_the_default_ground_truth_source_never_answers() -> None:
    """既定の引き口が常に ``None`` を返すこと。

    記入者が決まるまで、正解は存在しない。
    """
    source = NoGroundTruth()

    assert source.lookup("case-1", "何でも") is None


def test_scoring_without_ground_truth_reports_no_hit_rate() -> None:
    """正解が引けないときに的中率を出さないこと。"""
    candidates = _candidates(10) + _candidates(10, tier=1)
    plan = plan_tiered_audit(candidates, policies=BOTH_TIERS, seed=0, now=FIXED_NOW)

    report = score_audit_plan(plan, NoGroundTruth())

    for tier in (1, 2):
        assert report.report_for(tier).hit_rate is None
        assert report.report_for(tier).status == "awaiting_human"
    assert report.found_error is False


def test_an_empty_population_stays_distinct_from_a_clean_audit() -> None:
    """母集団0件を「監査して全部当たった」と混同しないこと。"""
    plan = plan_tiered_audit([], policies=BOTH_TIERS, seed=0, now=FIXED_NOW)

    assert plan.plan_for(1).status == "no_population"
    assert plan.plan_for(2).status == "no_population"
    assert plan.plan_for(2).sample_size == 0

    report = score_audit_plan(plan, NoGroundTruth())
    assert report.report_for(2).status == "no_population"
    assert report.report_for(2).hit_rate is None


def test_a_ground_truth_source_object_is_accepted() -> None:
    """``lookup`` を持つ引き口をそのまま渡せること。"""

    class _Store:
        def lookup(self, case_id: str, target: str) -> int | None:
            assert case_id == "case-7"
            return 5 if target.endswith("00") else None

    candidates = _candidates(10)
    plan = plan_tiered_audit(
        candidates, policies={2: TierAuditPolicy(1.0, 1)}, seed=0, now=FIXED_NOW,
        case_id="case-7",
    )
    report = score_audit_plan(plan, _Store())
    tier2 = report.report_for(2)

    assert tier2.sample_size == 10
    # 正解が引けたのは t2_00 の1件だけ。残り9件は的中率の分母に入らない。
    assert tier2.verifiable_count == 1
    assert tier2.hit_rate == 1.0
    assert tier2.unverifiable_count == 9


# =====================================================================
# 記録の形
# =====================================================================


def test_the_plan_records_what_is_needed_to_stack_cases_later() -> None:
    """記録に、案件をまたいで積み上げるのに要るものが入っていること。

    同じ図面の同じ読みに対する監査を二重に数えないために、図面の指紋が要る。
    """
    candidates = _candidates(10, with_provenance=True)
    plan = plan_tiered_audit(
        candidates, policies=BOTH_TIERS, seed=9, now=FIXED_NOW,
        case_id="case-1", source_fingerprint="abc123", start_kit_fingerprint="def456",
    )
    record = plan.as_log_dict()

    assert record["case_id"] == "case-1"
    assert record["source_fingerprint"] == "abc123"
    assert record["start_kit_fingerprint"] == "def456"
    assert record["seed"] == 9
    assert record["recorded_at"].startswith("2026-09-22T06:00:00")
    assert record["tiers"]["2"]["population"] == 10
    assert record["tiers"]["2"]["sample"] == 5
    assert record["tiers"]["1"]["status"] == "no_population"
    sampled = record["tiers"]["2"]["sampled"]
    assert len(sampled) == 5
    assert sampled[0]["provenance"]["page_number"] >= 1
    assert sampled[0]["adopted_range"] == [4, 6]


def test_the_record_is_json_serialisable() -> None:
    """記録がそのまま JSON に書けること。"""
    import json

    plan = plan_tiered_audit(
        _candidates(6, with_provenance=True), policies=BOTH_TIERS, seed=0,
        now=FIXED_NOW, case_id="case-1",
    )

    assert json.loads(json.dumps(plan.as_log_dict()))["case_id"] == "case-1"


def test_a_tier_without_a_policy_is_still_refused_when_planning() -> None:
    """方針を決めていない階層が母集団に混ざったら、抽出の時点で止めること。"""
    with pytest.raises(AuditConfigurationError):
        plan_tiered_audit(
            _candidates(5) + _candidates(5, tier=1),
            policies={2: TierAuditPolicy()}, seed=0, now=FIXED_NOW,
        )


def test_planning_is_deterministic_for_the_same_seed() -> None:
    """同じ母集団・同じシードなら、抜き取られる対象が変わらないこと。

    人が確かめている最中に一覧が入れ替わると、確かめた分が捨てられる。
    """
    candidates = _candidates(20)
    first = plan_tiered_audit(candidates, policies=BOTH_TIERS, seed=4, now=FIXED_NOW)
    second = plan_tiered_audit(
        list(reversed(candidates)), policies=BOTH_TIERS, seed=4, now=FIXED_NOW
    )

    assert [c.target for c in first.plan_for(2).sampled] == [
        c.target for c in second.plan_for(2).sampled
    ]
    third = plan_tiered_audit(candidates, policies=BOTH_TIERS, seed=5, now=FIXED_NOW)
    assert [c.target for c in third.plan_for(2).sampled] != [
        c.target for c in first.plan_for(2).sampled
    ]


def test_the_plan_type_is_what_the_intake_will_hold() -> None:
    """入口が持ち回る型が公開されていること。"""
    plan = plan_tiered_audit([], policies=BOTH_TIERS, seed=0, now=FIXED_NOW)

    assert isinstance(plan, TieredAuditPlan)
    assert plan.audited_tiers == (1, 2)
