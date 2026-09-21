"""階層2(仮採用+抜き取り監査)の抜き取り監査の、検出力の実測。

何を測るのか
------------
抜き取り監査は**誤りの存在を確率的にしか検出できない。** このスクリプトは
「抽出率をいくつにすると、どれだけ捕まえられるのか」を実測し、既定値
(30%・最小5件)の根拠を残す。

**「監査しているから安全」という言い方ができないことを、数値で示すのが目的**
である。

実行: ``python -m benchmarks.run_provisional_audit_eval``
"""

from __future__ import annotations

import argparse

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.provisional_audit import (
    DEFAULT_MINIMUM_SAMPLE,
    DEFAULT_SAMPLING_RATE,
    AuditCandidate,
    collect_tier2_population,
    format_report,
    plan_sample_size,
    run_provisional_audit,
)

#: 想定する階層2の誤り率。トライアル8で「階層なしでは精度100%→78.9%」と
#: 報告されているが、これは Codex 側の概念実証の数値であり本リポジトリでは
#: 再現していない(`docs/audit_unimplemented_trial_claims.md` 4節)。
#: ここでは幅を持たせて複数の誤り率で測る。
ASSUMED_ERROR_RATES = (0.02, 0.05, 0.10, 0.20)


def _tier2_evidence(target: str) -> list[AxisEvidence]:
    """階層2(強い軸1つ+独立した弱い軸2つ)になる証拠の組。"""
    return [
        AxisEvidence(target=target, count_range=(5, 5), source_id="drawing-A",
                     axis_id="image", method_id="room_detector", unit="count",
                     calibrated=True),
        AxisEvidence(target=target, count_range=(4, 6), source_id="自社実績DB",
                     axis_id="history", method_id="past_projects", unit="count",
                     strength="weak", calibrated=True),
        AxisEvidence(target=target, count_range=(4, 6), source_id="rule-A",
                     axis_id="rules", method_id="rule_ratio", unit="count",
                     strength="weak", calibrated=True),
    ]


def _population_through_the_real_firewall(size: int) -> tuple[AuditCandidate, ...]:
    """実際のファイアウォールを通して母集団を作る。

    監査が現実の階層2判定とつながっていることを、ベンチマーク側でも確かめる。
    """
    firewall = AxisQualityFirewall()
    assessments = {}
    for index in range(size):
        target = f"element_{index:03d}"
        evidences = _tier2_evidence(target)
        decision = firewall.assess(evidences)
        assert decision.action == "provisional_audit", decision.action
        assessments[target] = (decision, evidences)
    return collect_tier2_population(assessments)


def _detection_table(population_sizes: tuple[int, ...]) -> None:
    print("=== 検出力: 誤りが1件以上見つかる確率(超幾何分布) ===")
    print()
    header = "母集団   抽出   " + "  ".join(f"誤り{r:.0%}" for r in ASSUMED_ERROR_RATES)
    print(header)
    print("-" * len(header))
    for size in population_sizes:
        sample = plan_sample_size(size)
        candidates = [
            AuditCandidate(target=f"e{i}", adopted_range=(4, 6), unit="count",
                           category="room_detector")
            for i in range(size)
        ]
        report = run_provisional_audit(
            candidates, {c.target: 5 for c in candidates}, seed=0
        )
        cells = []
        for rate in ASSUMED_ERROR_RATES:
            errors = max(1, round(size * rate))
            cells.append(f"{report.detection_probability(errors):>6.1%}")
        print(f"{size:>6}   {sample:>4}   " + "  ".join(cells))
    print()
    print("読み方: 母集団が小さいほど、1件の誤りは見つけにくい。")
    print("      抽出率を上げるより、母集団が大きいことのほうが効く。")
    print()


def _minimum_sample_effect() -> None:
    print("=== 最小抽出件数(既定5件)を入れた効果 ===")
    print()
    print("母集団   率のみ   最小5件   検出確率(誤り1件)")
    print("-" * 46)
    for size in (5, 8, 10, 15, 20, 30):
        candidates = [
            AuditCandidate(target=f"e{i}", adopted_range=(4, 6), unit="count",
                           category="room_detector")
            for i in range(size)
        ]
        truth = {c.target: 5 for c in candidates}
        rate_only = run_provisional_audit(candidates, truth, seed=0, minimum_sample=1)
        with_floor = run_provisional_audit(candidates, truth, seed=0)
        print(
            f"{size:>6}   {rate_only.sample_size:>6}   {with_floor.sample_size:>7}   "
            f"{rate_only.detection_probability(1):>6.1%} → "
            f"{with_floor.detection_probability(1):>6.1%}"
        )
    print()


def _worked_example(size: int, planted_errors: int, seed: int) -> None:
    print(f"=== 実例: 母集団{size}件・誤り{planted_errors}件を仕込んだ監査 ===")
    print()
    population = _population_through_the_real_firewall(size)
    truth = {c.target: 5 for c in population}
    for candidate in population[:planted_errors]:
        truth[candidate.target] = 99  # 採用範囲 (5,5) の外
    report = run_provisional_audit(population, truth, seed=seed)
    print(format_report(report))
    print()
    if not report.found_error:
        print(
            f"※ この試行では誤りを捕まえられなかった(シード{seed})。"
            "見逃しは仕様どおりの挙動であって、不具合ではない。"
        )
        print()


def _miss_rate_over_seeds(size: int, planted_errors: int, trials: int) -> None:
    """実際に何回見逃すかを、シードを振って数える。"""
    print(f"=== 実測: 母集団{size}件・誤り{planted_errors}件で{trials}回試行 ===")
    print()
    population = _population_through_the_real_firewall(size)
    truth = {c.target: 5 for c in population}
    for candidate in population[:planted_errors]:
        truth[candidate.target] = 99
    caught = sum(
        1 for seed in range(trials)
        if run_provisional_audit(population, truth, seed=seed).found_error
    )
    observed = caught / trials
    theoretical = run_provisional_audit(
        population, truth, seed=0
    ).detection_probability(planted_errors)
    print(f"捕まえた回数   : {caught}/{trials}({observed:.1%})")
    print(f"理論値(超幾何) : {theoretical:.1%}")
    print(f"実測の見逃し率 : {1 - observed:.1%}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=200)
    args = parser.parse_args()

    print(f"既定の抽出率: {DEFAULT_SAMPLING_RATE:.0%}(最小{DEFAULT_MINIMUM_SAMPLE}件)")
    print()
    _detection_table((5, 10, 20, 50, 100, 200, 500))
    _minimum_sample_effect()
    _worked_example(size=20, planted_errors=2, seed=0)
    _miss_rate_over_seeds(size=20, planted_errors=1, trials=args.trials)


if __name__ == "__main__":
    main()
