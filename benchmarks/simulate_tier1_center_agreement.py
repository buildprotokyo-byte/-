"""階層1の確定条件を厳しくしたときに、精度がどう変わるかのシミュレーション。

**これは提案のシミュレーションであって、本実装ではない。**
`arbitration/` と `killer_question/` には一切手を入れず、ファイアウォールの
判定結果を**後ろから書き換える**ことで提案を再現する。おーちゃんの依頼
(2026-09-21)で、実装前に効果を測ることが目的。

再現したい提案は2つ:

A. **中心値の一致を階層1の追加条件にする。** レンジが重なっていても、独立した
   強いデータ源の**中心値**が許容差を超えて離れていれば階層1にしない。
   外れたときの行き先は2通り考えられるので、両方測る
   (`tier3` = 人が必ず確認 / `tier2` = 仮採用して抜き取り監査)。
B. **等式でクラスタ全体に伝播する値は階層1を経由させない。** クラスタに属する
   要素が階層1になった場合、階層2(仮採用+抜き取り監査)へ落とす。

注意: **階層1と階層2は、ソルバーに入る定義域が同じである**
(`firewall_bridge` はどちらも `decision.confirmed_range` を登録する)。
したがって階層2へ落とすこと自体は、ソルバーが出す値を変えない。効くのは
**抜き取り監査で捕まえられるようになる**経路だけである。そこを測るために、
本スクリプトは既存ベンチマークには無い「監査が誤りを捕まえて人が直す」
段階まで回す。
"""

from __future__ import annotations

import dataclasses
import json
import statistics
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbitration.axis_quality_firewall import (
    CENTER_TOLERANCES,
    AxisEvidence,
    AxisQualityFirewall,
    CenterTolerance,
)
from arbitration.consistency_solver import ConsistencySolver
from arbitration.provisional_audit import (
    collect_tier2_population,
    expand_to_categories,
    run_provisional_audit,
)
from benchmarks.run_trial789_reproduction import (
    AXIS_ERROR_RATES,
    Scenario,
    _link_clusters,
    _variables_in_conflict,
    _MAX_CONTRADICTION_ROUNDS,
    build_scenario,
    make_evidence,
    run_without_tiers,
)
from killer_question.engine import KillerQuestionEngine
from killer_question.firewall_bridge import add_target_to_joint_solver

MismatchTarget = Literal["tier2", "tier3"]


#: 本体の中心値検査を実質的に無効化する許容差表。
#:
#: **なぜこれが要るのか。** 本スクリプトの各方針は「中心値の検査が
#: まだ無かった頃のファイアウォール」に提案を**後ろから**当てて効果を測る
#: ものである。ところが 2026-09-21 に検査を本体
#: (`arbitration/axis_quality_firewall.py`)へ実装したため、何も指定しないと
#: 本体の検査と後ろからの書き換えが**二重に効き**、「現行」の列が現行を
#: 表さなくなった。実際、実装後にこのスクリプトを走らせると8方針すべてが
#: 同一の数値になり、実装前の劣化(下回り 10/30・25/30)が再現できない。
#:
#: 無効化は本体に迂回用のスイッチを足すのではなく、既にある入口
#: (`AxisQualityFirewall(center_tolerances=...)`)へ極端に大きい相対許容差を
#: 渡して行う。**表に無い単位は既定の許容差に落ちるので完全な無効化では
#: ない。** 本シナリオが使う単位は `count` だけで、それは表に入っている
#: (`_assert_units_are_covered` で確認する)。
CHECK_DISABLED: dict[str, CenterTolerance] = {
    unit: CenterTolerance(relative=Fraction(10 ** 9)) for unit in CENTER_TOLERANCES
}


def _assert_units_are_covered(evidence: dict[str, list[AxisEvidence]]) -> None:
    """無効化の表が、このシナリオの使う単位を全部覆っているか。

    覆えていない単位があると、そこだけ本体の検査が生き残り、「検査なし」の
    列が静かに「検査あり」になる。黙って進めない。
    """
    used = {item.unit for items in evidence.values() for item in items}
    missing = used - set(CHECK_DISABLED)
    if missing:
        raise AssertionError(
            f"中心値の検査を無効化できない単位がある(既定の許容差に落ちる): {sorted(missing)}"
        )


@dataclass(frozen=True)
class TierPolicy:
    """階層1の確定条件。``現行`` は両方とも無効にしたもの。"""

    name: str
    #: 独立した強いデータ源の中心値の許容差。``None`` なら中心値を見ない。
    center_tolerance: float | None = None
    #: 中心値が離れていたときの行き先。
    on_center_mismatch: MismatchTarget = "tier3"
    #: クラスタに属する階層1を階層2へ落とすか(提案B)。
    cluster_to_tier2: bool = False
    #: **提案A':中心値の検査を階層2にも適用するか。**
    #: 実測でこのシナリオの劣化は階層1ではなく**階層2**から来ていたため
    #: (下記 `_all_source_centers` のコメント)、提案Aの意図を届かせるには
    #: ここが必要になる。階層1だけに入れても、このシナリオでは何も変わらない。
    apply_to_tier2: bool = False
    #: **本体(`AxisQualityFirewall`)の中心値検査を使うか。**
    #: False のとき `CHECK_DISABLED` を渡して本体の検査を実質的に止め、
    #: 「検査がまだ無かった頃」を再現する。提案を後ろから当てる方針
    #: (A1/A2/B/A')は、二重に効かせないため必ず False にする。
    #: True にして後ろからの書き換えを一切しない方針が「本実装」である。
    use_builtin_check: bool = False


def _source_ranges(evidences: list[AxisEvidence]) -> list[tuple[int, int]]:
    """ファイアウォールと同じ手順で、データ源ごとのレンジを組み立てる。

    ``AxisQualityFirewall.assess()` の ``usable_sources`` と同じ計算をする。
    同じ source の手法同士が矛盾する場合はハードから降格されるので除く。
    """
    by_source: dict[str, list[AxisEvidence]] = {}
    for item in evidences:
        if not item.is_hard_eligible:
            continue
        by_source.setdefault(item.independence_key, []).append(item)
    ranges: list[tuple[int, int]] = []
    for items in by_source.values():
        lower = max(i.count_range[0] for i in items)
        upper = min(i.count_range[1] for i in items)
        if lower <= upper:
            ranges.append((lower, upper))
    return ranges


def _centers_disagree(evidences: list[AxisEvidence], tolerance: float) -> bool:
    """独立した強いデータ源の中心値が、許容差を超えて離れているか(提案A)。"""
    centers = [(low + high) / 2 for low, high in _source_ranges(evidences)]
    if len(centers) < 2:
        return False
    return (max(centers) - min(centers)) > tolerance


def _all_source_centers(evidences: list[AxisEvidence]) -> list[float]:
    """棄権していない**全データ源**(強い軸+弱い軸)の中心値。

    階層2は「強い軸1つ+それを支持する弱い軸2つ以上」で成立する。実測では、
    強い軸が誤った重なるレンジを出しているのに弱い軸が正解側を指している
    ケースで、**強い軸のレンジがそのまま採用範囲になっていた**
    (例: image=(3,4)、history=rules=(4,6)、正解5)。強い軸同士だけを見る
    提案Aでは、独立した強い軸が1つしか無いこの形を検出できない。
    """
    by_source: dict[str, list[AxisEvidence]] = {}
    for item in evidences:
        if item.status == "abstained":
            continue
        by_source.setdefault(item.independence_key, []).append(item)
    centers: list[float] = []
    for items in by_source.values():
        lower = min(i.count_range[0] for i in items)
        upper = max(i.count_range[1] for i in items)
        centers.append((lower + upper) / 2)
    return centers


def _all_centers_disagree(evidences: list[AxisEvidence], tolerance: float) -> bool:
    centers = _all_source_centers(evidences)
    if len(centers) < 2:
        return False
    return (max(centers) - min(centers)) > tolerance


def apply_policy(decision, evidences: list[AxisEvidence], *,
                 in_cluster: bool, policy: TierPolicy):
    """ファイアウォールの判定に提案A・A'・Bを後ろから適用する。"""
    if decision.action == "provisional_audit":
        # 提案A': 階層2にも中心値の検査を入れる。外れたら階層3(人が確認)。
        if (policy.apply_to_tier2 and policy.center_tolerance is not None
                and _all_centers_disagree(evidences, policy.center_tolerance)):
            return dataclasses.replace(
                decision, tier=3, action="requires_review", confirmed_range=None,
                reasons=decision.reasons + ("階層2: 中心値が許容差を超えて離れている",),
            )
        return decision
    if decision.action != "auto_confirm":
        return decision  # 階層3はそのまま

    if policy.center_tolerance is not None and _centers_disagree(
        evidences, policy.center_tolerance
    ):
        if policy.on_center_mismatch == "tier3":
            return dataclasses.replace(
                decision, tier=3, action="requires_review", confirmed_range=None,
                reasons=decision.reasons + ("中心値が許容差を超えて離れている",),
            )
        return dataclasses.replace(
            decision, tier=2, action="provisional_audit",
            reasons=decision.reasons + ("中心値が許容差を超えて離れている",),
        )

    if policy.cluster_to_tier2 and in_cluster:
        return dataclasses.replace(
            decision, tier=2, action="provisional_audit",
            reasons=decision.reasons + ("等式でクラスタ全体に伝播するため階層2を経由",),
        )
    return decision


@dataclass(frozen=True)
class SimOutcome:
    #: ソルバーが出した値の正しさ(抜き取り監査による訂正の**前**)。
    accuracy_before_audit: float
    #: 抜き取り監査が誤りを捕まえて人が直した**後**の正しさ。
    accuracy_after_audit: float
    cost: int
    questions: int
    tier3_checks: int
    tier2_population: int
    audit_samples: int
    #: 監査が実際に捕まえた誤りの件数。
    audit_catches: int
    #: 拡大監査(v8 4-3節ルール3)で追加確認した件数。
    expanded_checks: int
    contradiction_checks: int


def _build(scenario: Scenario, evidence, confirmed, policy: TierPolicy):
    firewall = AxisQualityFirewall(
        center_tolerances=None if policy.use_builtin_check else CHECK_DISABLED
    )
    solver = ConsistencySolver()
    assessments: dict[str, tuple[object, list[AxisEvidence]]] = {}
    tier3: list[str] = []
    clustered = {m for members in scenario.clusters if len(members) > 1 for m in members}

    for name, items in evidence.items():
        if name in confirmed:
            value = confirmed[name]
            solver.add_variable(name, value, value, axis="human_confirmed", unit="count")
            continue
        decision = firewall.assess(items)
        decision = apply_policy(
            decision, items, in_cluster=name in clustered, policy=policy
        )
        assessments[name] = (decision, items)
        registered = add_target_to_joint_solver(solver, name, decision, items)
        if not registered.registered:
            tier3.append(name)
    _link_clusters(solver, scenario)
    return solver, assessments, tier3


def _human_checked(tier3: Sequence[str], session) -> set[str]:
    """人が1件ずつ確認する要素の集合。

    **2026-09-22 に数え方を直した。** それまでは「変数として登録されなかった
    要素」だけを数えていたが、これは当時
    ``killer_question/firewall_bridge.py`` が階層3の一部を登録していなかった
    ことに依存した代理指標だった。停止した要素も必ず登録するようになった
    (群合計制約が書けなくなるため。`docs/group_total_masking_design.md`)ので、
    この代理は成り立たない。

    数えるべきは **``requires_confirmation`` が立ったまま残った要素**である。
    質問で解決された要素は ``mark_confirmed()`` でフラグが降りるので、
    ``session.question_count`` との二重計上にはならない。
    """
    return set(tier3) | set(session.final_solver.names_requiring_confirmation())


def run_with_policy(scenario: Scenario, evidence, *, policy: TierPolicy,
                    audit_seed: int) -> SimOutcome:
    confirmed: dict[str, int] = {}
    contradiction_checks = 0
    rounds = 0
    while rounds < _MAX_CONTRADICTION_ROUNDS:
        solver, assessments, tier3 = _build(scenario, evidence, confirmed, policy)
        probe = solver.solve()
        if probe.is_consistent:
            break
        rounds += 1
        culprits = _variables_in_conflict(solver, probe.conflicting_constraints)
        culprits -= set(confirmed)
        if not culprits:
            break
        for name in sorted(culprits):
            confirmed[name] = scenario.truth[name]
            contradiction_checks += 1
    else:  # pragma: no cover
        solver, assessments, tier3 = _build(scenario, evidence, confirmed, policy)

    engine = KillerQuestionEngine(solver, axis_error_rates=AXIS_ERROR_RATES)
    session = engine.run(lambda question: scenario.truth[question.variable])
    result = session.final_result
    human_checked = _human_checked(tier3, session)

    def wrong_targets(res, extra_confirmed: set[str]) -> list[str]:
        out = []
        for name, value in scenario.truth.items():
            if name in human_checked or name in confirmed or name in extra_confirmed:
                continue
            solution = res.variables.get(name)
            if solution is None or solution.solved_range != (value, value):
                out.append(name)
        return out

    before = wrong_targets(result, set())

    # --- 抜き取り監査を実際に回す ---------------------------------------
    population = collect_tier2_population(assessments)
    answered = {a.variable for a in session.answered} | set(confirmed)
    remaining = tuple(c for c in population if c.target not in answered)
    report = run_provisional_audit(remaining, scenario.truth, seed=audit_seed)
    mismatches = [f for f in report.findings if f.outcome == "mismatch"]

    # v8 4-3節ルール3: 誤りが出たカテゴリは抽出外も全件確認する。
    expanded = expand_to_categories(remaining, [f.category for f in mismatches])
    sampled_names = set(report.log.sampled_targets)
    extra_candidates = [c for c in expanded if c.target not in sampled_names]
    extra = {c.target for c in extra_candidates}
    caught = {f.target for f in mismatches}
    for candidate in extra_candidates:
        # 拡大監査も正解と照合する。外れていれば人が直す対象。
        if not candidate.contains(scenario.truth[candidate.target]):
            caught.add(candidate.target)

    # 捕まえた誤りを人が直して、クラスタへ伝播させ直す。
    after = before
    if caught:
        recheck = dict(confirmed)
        for name in caught:
            recheck[name] = scenario.truth[name]
        solver2, _, tier3b = _build(scenario, evidence, recheck, policy)
        engine2 = KillerQuestionEngine(solver2, axis_error_rates=AXIS_ERROR_RATES)
        session2 = engine2.run(lambda q: scenario.truth[q.variable])
        human_checked2 = _human_checked(tier3b, session2)
        after = [
            n for n, v in scenario.truth.items()
            if n not in human_checked2 and n not in recheck
            and (session2.final_result.variables.get(n) is None
                 or session2.final_result.variables[n].solved_range != (v, v))
        ]

    cost = (session.question_count + len(human_checked) + report.sample_size
            + contradiction_checks + len(extra))
    return SimOutcome(
        accuracy_before_audit=1 - len(before) / scenario.size,
        accuracy_after_audit=1 - len(after) / scenario.size,
        cost=cost, questions=session.question_count, tier3_checks=len(human_checked),
        tier2_population=len(remaining), audit_samples=report.sample_size,
        audit_catches=len(caught), expanded_checks=len(extra),
        contradiction_checks=contradiction_checks,
    )


# =====================================================================
# 比較の実行
# =====================================================================

#: 比較する方針。現行を先頭に置く。
#:
#: おーちゃんの提案そのままが A1/A2/B。**実測でこのシナリオの劣化が階層1では
#: なく階層2から来ていたことが分かったため**、意図を届かせる案として
#: A' (中心値の検査を階層2にも適用)も並べて測る。
POLICIES: tuple[TierPolicy, ...] = (
    TierPolicy("現行"),
    TierPolicy("A1 中心±1→階層3", center_tolerance=1.0, on_center_mismatch="tier3"),
    TierPolicy("A2 中心±1→階層2", center_tolerance=1.0, on_center_mismatch="tier2"),
    TierPolicy("B クラスタ→階層2", cluster_to_tier2=True),
    TierPolicy("A1+B 中心±1", center_tolerance=1.0,
               on_center_mismatch="tier3", cluster_to_tier2=True),
    TierPolicy("A' 階層2にも中心±1", center_tolerance=1.0, apply_to_tier2=True),
    TierPolicy("A' 階層2にも中心±0", center_tolerance=0.0, apply_to_tier2=True),
    TierPolicy("A'+B 中心±1", center_tolerance=1.0,
               apply_to_tier2=True, cluster_to_tier2=True),
    #: **本実装。** 後ろからの書き換えを一切せず、
    #: `arbitration/axis_quality_firewall.py` に入った検査をそのまま使う。
    #: 予測(A' 階層2にも中心±1)と一致するかを、この列で突き合わせる。
    TierPolicy("本実装(本体の中心値検査)", use_builtin_check=True),
)


#: 計測結果の保存先。回帰テストはこのファイルの数値を固定値として突き合わせる。
RESULT_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "center_agreement_effect_result.json"
)


def condition_key(model: str, with_weak_axes: bool) -> str:
    """条件の識別子。報告書の表の行と1対1で対応させる。"""
    shape = "weak" if with_weak_axes else "strong2"
    return f"{shape}/{model}"


#: 測れる条件の一覧。**条件ごとに別プロセスで並列に回せる。**
#: 1プロセスで直列に回すと、この規模(30試行 x 9方針)で1条件に数十分かかる。
#: 条件どうしは独立で、乱数シードも条件ごとに 0〜trials-1 を使うだけなので、
#: 並列にしても結果は変わらない(`--only` を使った結果と直列の結果が一致する
#: ことを `tests/test_center_agreement_effect.py` で確認する)。
ALL_CONDITIONS: tuple[tuple[str, bool], ...] = (
    ("overlapping", True), ("disjoint", True),
    ("overlapping", False), ("disjoint", False),
)


def compare(model: str, trials: int, *, with_weak_axes: bool) -> dict[str, object]:
    scenario = build_scenario()
    shape = ("強い軸1つ+弱い軸2つ(階層2ができる形)" if with_weak_axes
             else "強い軸2つ(階層1ができる形)")
    print("=" * 78)
    print(f"誤りの入れ方: {model} / 証拠の形: {shape} / {trials}試行")
    print("=" * 78)
    print()

    baseline: list[float] = []
    rows: dict[str, dict[str, list[float]]] = {
        p.name: {"before": [], "after": [], "cost": [], "tier3": [],
                 "tier2": [], "catches": []}
        for p in POLICIES
    }
    worse_before: dict[str, int] = {p.name: 0 for p in POLICIES}
    worse_after: dict[str, int] = {p.name: 0 for p in POLICIES}

    #: 試行ごとの生の値。要約だけを凍結すると、数が合っていても中身が
    #: 入れ替わっている場合に気づけない。
    per_seed: dict[str, list[dict[str, float]]] = {p.name: [] for p in POLICIES}
    baseline_per_seed: list[float] = []

    for seed in range(trials):
        evidence = make_evidence(
            scenario, seed, model,
            with_weak_axes=with_weak_axes, mixed_primary_axes=True,
        )
        _assert_units_are_covered(evidence)
        plain = run_without_tiers(scenario, evidence)
        baseline.append(plain.accuracy)
        baseline_per_seed.append(plain.accuracy)
        for policy in POLICIES:
            out = run_with_policy(
                scenario, evidence, policy=policy, audit_seed=seed
            )
            row = rows[policy.name]
            row["before"].append(out.accuracy_before_audit)
            row["after"].append(out.accuracy_after_audit)
            row["cost"].append(out.cost)
            row["tier3"].append(out.tier3_checks)
            row["tier2"].append(out.tier2_population)
            row["catches"].append(out.audit_catches)
            if out.accuracy_before_audit < plain.accuracy:
                worse_before[policy.name] += 1
            if out.accuracy_after_audit < plain.accuracy:
                worse_after[policy.name] += 1
            per_seed[policy.name].append({
                "seed": seed,
                "baseline": plain.accuracy,
                "before": out.accuracy_before_audit,
                "after": out.accuracy_after_audit,
                "cost": out.cost,
            })

    print(f"階層なしの平均精度: {statistics.mean(baseline):.1%}")
    print()
    header = (f"{'方針':<22}{'監査前':>8}{'監査後':>8}{'コスト':>8}"
              f"{'階層3':>7}{'階層2':>7}{'捕捉':>6}{'下回り前':>9}{'下回り後':>9}")
    print(header)
    print("-" * 86)
    for policy in POLICIES:
        row = rows[policy.name]
        print(f"{policy.name:<22}"
              f"{statistics.mean(row['before']):>8.1%}"
              f"{statistics.mean(row['after']):>8.1%}"
              f"{statistics.mean(row['cost']):>8.1f}"
              f"{statistics.mean(row['tier3']):>7.1f}"
              f"{statistics.mean(row['tier2']):>7.1f}"
              f"{statistics.mean(row['catches']):>6.1f}"
              f"{worse_before[policy.name]:>9}"
              f"{worse_after[policy.name]:>9}")
    print()
    print("  下回り前/後 = 階層なしより精度が低かった試行数(監査の訂正 前/後)")
    print()

    return {
        "condition": condition_key(model, with_weak_axes),
        "shape": shape,
        "model": model,
        "trials": trials,
        "baseline_accuracy": statistics.mean(baseline),
        "baseline_per_seed": baseline_per_seed,
        "policies": {
            policy.name: {
                "accuracy_before_audit": statistics.mean(rows[policy.name]["before"]),
                "accuracy_after_audit": statistics.mean(rows[policy.name]["after"]),
                "cost": statistics.mean(rows[policy.name]["cost"]),
                "worse_than_no_tiers_before": worse_before[policy.name],
                "worse_than_no_tiers_after": worse_after[policy.name],
                "per_seed": per_seed[policy.name],
            }
            for policy in POLICIES
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument(
        "--json", type=Path, default=None,
        help=f"計測結果の保存先(省略時は書き出さない。既定の置き場は {RESULT_PATH})",
    )
    parser.add_argument(
        "--only", action="append", default=None,
        choices=[condition_key(m, w) for m, w in ALL_CONDITIONS],
        help="この条件だけを測る。**条件ごとに別プロセスで並列に回すために使う。**"
             " 複数指定できる。省略すると4条件すべてを直列に回す(遅い)",
    )
    args = parser.parse_args()
    wanted = (
        [c for c in ALL_CONDITIONS if condition_key(*c) in set(args.only)]
        if args.only else list(ALL_CONDITIONS)
    )
    conditions: list[dict[str, object]] = []
    for model, with_weak in wanted:
        conditions.append(compare(model, args.trials, with_weak_axes=with_weak))
    if args.json is not None:
        payload = {
            "trials": args.trials,
            "policies": [p.name for p in POLICIES],
            "conditions": conditions,
        }
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"計測結果を書き出しました: {args.json}")


if __name__ == "__main__":
    main()
