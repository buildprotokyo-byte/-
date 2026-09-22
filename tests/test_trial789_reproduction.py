"""トライアル7・8・9の再現ベンチマークの回帰テスト。

`benchmarks/run_trial789_reproduction.py` が測っている性質を縛る。
**数値そのものの一致ではなく、「シナリオが意図した形になっているか」と
「測定のからくりが壊れていないか」を縛る**のが目的。

再現ベンチマークが静かに無意味になる道が2つあるので、そこを重点的に見る。

1. シナリオが意図した構造になっていない(19要素6クラスタでない、理論下限が
   6でない)。この場合「理論下限と一致した」は何も意味しない
2. 誤りを入れたつもりで入っていない。この場合「階層が精度を回復させた」が
   自動的に成り立ってしまう
"""

from __future__ import annotations

import pytest

from benchmarks.run_trial789_reproduction import (
    AXIS_ERROR_RATES,
    CLUSTERS,
    _build_joint_solver,
    build_scenario,
    make_evidence,
    run_with_tiers,
    run_without_tiers,
    theoretical_lower_bound,
)


# =====================================================================
# シナリオが意図した構造になっている
# =====================================================================


def test_the_scenario_has_19_elements_in_6_clusters() -> None:
    """トライアル7と同じ規模(19要素)であること。"""
    scenario = build_scenario()
    assert scenario.size == 19
    assert len(scenario.clusters) == 6
    assert sum(size for _, size in CLUSTERS) == 19
    # クラスタに重複が無い
    flat = [m for cluster in scenario.clusters for m in cluster]
    assert len(flat) == len(set(flat)) == 19


def test_each_cluster_shares_one_ground_truth_value() -> None:
    """クラスタ内は同数(等式で結べる)であること。"""
    scenario = build_scenario()
    for cluster in scenario.clusters:
        values = {scenario.truth[m] for m in cluster}
        assert len(values) == 1, cluster


def test_clusters_have_distinct_values_so_cross_talk_is_visible() -> None:
    """クラスタ間は違う値であること。

    全クラスタが同じ値だと、クラスタをまたいだ誤った伝播が起きても
    正解に見えてしまい、測定が意味をなさなくなる。
    """
    scenario = build_scenario()
    per_cluster = [scenario.truth[cluster[0]] for cluster in scenario.clusters]
    assert len(set(per_cluster)) == len(per_cluster)


def test_the_lower_bound_is_computed_not_hardcoded() -> None:
    """理論下限が依存グラフから計算され、クラスタ数と一致すること。"""
    scenario = build_scenario()
    assert theoretical_lower_bound(scenario) == len(scenario.clusters) == 6


# =====================================================================
# トライアル7: 理想条件で理論下限に一致する
# =====================================================================


def test_trial7_reaches_the_lower_bound_with_full_accuracy() -> None:
    """誤りが無ければ、理論下限の質問数で全要素が正解になること。"""
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=False)
    outcome = run_with_tiers(scenario, evidence, use_axis_error_rates=False)

    assert outcome.questions == theoretical_lower_bound(scenario) == 6
    assert outcome.accuracy == 1.0
    assert outcome.contradiction_checks == 0
    assert outcome.wrong_targets == ()


# =====================================================================
# 誤りが実際に入っている(測定が空回りしていない)
# =====================================================================


def test_the_no_error_model_really_injects_nothing() -> None:
    """``model="none"`` では、全軸のレンジが正解を含むこと。"""
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=True)
    for name, items in evidence.items():
        for item in items:
            low, high = item.count_range
            assert low <= scenario.truth[name] <= high, (name, item.axis_id)


@pytest.mark.parametrize("model", ["disjoint", "overlapping"])
def test_the_error_models_really_inject_errors(model: str) -> None:
    """誤りを入れたつもりで入っていない、という空回りを防ぐ。

    これが入っていないと「階層が精度を回復させた」が自動的に成り立つ。
    """
    scenario = build_scenario()
    wrong_readings = 0
    for seed in range(5):
        evidence = make_evidence(scenario, seed, model, with_weak_axes=True)  # type: ignore[arg-type]
        for name, items in evidence.items():
            for item in items:
                low, high = item.count_range
                if not low <= scenario.truth[name] <= high:
                    wrong_readings += 1
    assert wrong_readings > 0, f"{model} で誤った読みが1件も生成されていない"


def test_the_disjoint_model_does_not_overlap_the_truth_range() -> None:
    """``disjoint`` の誤りが、正解のレンジと重ならないこと。

    重なってしまうと矛盾として検出できず、``overlapping`` と区別できなくなる。
    """
    scenario = build_scenario()
    for seed in range(5):
        evidence = make_evidence(scenario, seed, "disjoint", with_weak_axes=True)
        for name, items in evidence.items():
            truth = scenario.truth[name]
            truth_low, truth_high = max(1, truth - 1), truth + 1
            for item in items:
                low, high = item.count_range
                if low <= truth <= high:
                    continue  # 正しい読み
                # 誤った読みは、正解のレンジと重なっていないこと
                assert high < truth_low or low > truth_high, (name, item.count_range, truth)


def test_the_naive_baseline_actually_gets_things_wrong() -> None:
    """階層なしの精度が100%未満になること(比較の前提)。"""
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="disjoint", with_weak_axes=True)
    flat = run_without_tiers(scenario, evidence)

    assert flat.accuracy < 1.0
    assert flat.wrong_targets
    assert flat.cost == 0  # エスカレーションの経路が無いのが「階層なし」


def test_the_naive_baseline_is_perfect_without_errors() -> None:
    """誤りが無ければ階層なしでも100%になること。

    階層なしの精度低下が、誤りのせいであって実装の粗さではないことの確認。
    """
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=True)
    assert run_without_tiers(scenario, evidence).accuracy == 1.0


# =====================================================================
# 階層あり経路の測定のからくり
# =====================================================================


def test_weak_axes_are_required_for_a_tier2_population() -> None:
    """弱い軸が無いと階層2が生まれず、監査コストが測れないこと。

    これを知らずに弱い軸なしで測ると、監査件数0のまま
    「監査コストを測った」と誤って報告してしまう。
    """
    scenario = build_scenario()
    without_weak = make_evidence(scenario, seed=0, model="none", with_weak_axes=False)
    with_weak = make_evidence(scenario, seed=0, model="none", with_weak_axes=True)

    assert run_with_tiers(scenario, without_weak, use_axis_error_rates=False).tier2_population == 0
    assert run_with_tiers(scenario, with_weak, use_axis_error_rates=False).tier2_population > 0


def test_contradictions_are_detected_and_charged_to_the_cost() -> None:
    """矛盾が検出され、その解消回数がコストに入ること。

    矛盾検出(v8 3-2節)は成功であって失敗ではないが、**無料ではない。**
    """
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="disjoint", with_weak_axes=True)
    outcome = run_with_tiers(scenario, evidence, use_axis_error_rates=False)

    assert outcome.contradiction_checks > 0, "矛盾が1件も検出されていない"
    assert outcome.contradiction_rounds > 0
    assert outcome.cost >= outcome.contradiction_checks


def test_the_tiered_path_costs_more_than_the_ideal_case() -> None:
    """誤りがあると、理想条件よりコストが増えること。"""
    scenario = build_scenario()
    ideal = run_with_tiers(
        scenario, make_evidence(scenario, 0, "none", with_weak_axes=True),
        use_axis_error_rates=False,
    )
    noisy = run_with_tiers(
        scenario, make_evidence(scenario, 0, "disjoint", with_weak_axes=True),
        use_axis_error_rates=False,
    )
    assert noisy.cost > ideal.cost


def test_human_confirmed_elements_leave_the_audit_population() -> None:
    """人が確認した要素は、二重に監査されないこと。"""
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="disjoint", with_weak_axes=True)
    outcome = run_with_tiers(scenario, evidence, use_axis_error_rates=False)

    # 母集団は、確認済みを除いた残りなので全要素数より小さい
    assert outcome.tier2_population < scenario.size


def test_the_run_is_deterministic() -> None:
    """同じ入力で同じ結果になること(報告の再現に必要)。"""
    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=4, model="disjoint", with_weak_axes=True)
    first = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
    again = run_with_tiers(scenario, evidence, use_axis_error_rates=False)

    assert first == again


def test_evidence_generation_is_deterministic() -> None:
    scenario = build_scenario()
    a = make_evidence(scenario, seed=2, model="overlapping", with_weak_axes=True)
    b = make_evidence(scenario, seed=2, model="overlapping", with_weak_axes=True)
    assert a == b


# =====================================================================
# トライアル9のルールが、実運用の経路で発火すること
# =====================================================================


def test_the_bridge_preserves_the_originating_axis() -> None:
    """firewall_bridge を通しても、値の出どころの軸が残ること。

    **2026-09-21 に実測したバグの回帰テスト。** bridge は ``axis`` に
    確信度階層のラベル(``firewall_provisional`` 等)を入れるため、以前は
    元の軸が失われ、v8 4-3節ルール2(同じスコアなら誤り率の低いデータ源を
    優先する / トライアル9)が**実運用の経路で一度も発火しなかった。**
    """
    from killer_question.firewall_bridge import FIREWALL_PROVISIONAL_AXIS

    scenario = build_scenario()
    evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=True)
    solver, _, _ = _build_joint_solver(scenario, evidence, {})

    names = sorted(solver.variable_names())
    assert names, "前提: 変数が登録されている"

    # 階層のラベルは残っている(バグ②の回帰テストが依存している)
    labels = {solver.variable_axis(n) for n in names}
    assert FIREWALL_PROVISIONAL_AXIS in labels

    # そのうえで、誤り率を引くための軸名は元の軸になっている
    for name in names:
        assert solver.variable_error_rate_axis(name) in AXIS_ERROR_RATES, name


def test_the_axis_error_rate_rule_changes_the_question_order() -> None:
    """データ源別誤り率を渡すと、実際に質問の順序が変わること。

    順序が一切変わらなければ「監査コストが下がらなかった」という測定は、
    **ルールが発火していないだけ**で何も測れていないことになる。
    """
    scenario = build_scenario()
    changed = 0
    for seed in range(8):
        evidence = make_evidence(
            scenario, seed, "disjoint", with_weak_axes=True, mixed_primary_axes=True,
        )
        plain = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
        aware = run_with_tiers(scenario, evidence, use_axis_error_rates=True)
        if plain.asked != aware.asked:
            changed += 1
    assert changed > 0, "誤り率を渡しても質問順が一度も変わっていない"


def test_a_single_source_axis_cannot_exercise_the_rule() -> None:
    """全要素が同じ軸だと、このルールは発火しようがないこと。

    ``mixed_primary_axes=False`` のシナリオで「効果が無い」と結論するのは
    誤りである、という測定上の落とし穴を明示しておく。
    """
    scenario = build_scenario()
    evidence = make_evidence(
        scenario, 0, "disjoint", with_weak_axes=True, mixed_primary_axes=False,
    )
    solver, _, _ = _build_joint_solver(scenario, evidence, {})
    axes = {solver.variable_error_rate_axis(n) for n in solver.variable_names()}
    assert len(axes) == 1, "前提: 主軸を振り分けないと軸が1種類になる"

    plain = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
    aware = run_with_tiers(scenario, evidence, use_axis_error_rates=True)
    assert plain.asked == aware.asked  # 誤り率が全部同じなので差が出ない


def test_the_audit_sample_size_cannot_respond_at_this_scenario_scale() -> None:
    """トライアル9の「0.0%」を「効果が無い」と読まないための固定。

    ``plan_sample_size()`` は最小抽出件数5の床を持つので、階層2母集団が
    5〜18件の範囲では抽出件数が5に張り付いて動かない。本シナリオの母集団は
    その範囲に収まるため、**監査件数という指標は母集団の変化を映さない。**
    報告書4-5節の結論の根拠であり、床の値を変えたらここが落ちる。
    """
    from arbitration.provisional_audit import plan_sample_size

    constant = {n: plan_sample_size(n) for n in range(5, 19)}
    assert set(constant.values()) == {5}, f"床が動いている: {constant}"
    # 母集団が19件に達して初めて抽出率30%が床を上回る。
    assert plan_sample_size(18) == 5
    assert plan_sample_size(19) == 6


def test_the_rule_does_not_reduce_the_tier2_population_in_this_scenario() -> None:
    """ルールの効果の向きすら本シナリオでは確認できないことの固定。

    実測では母集団が**増える**方向に動いた試行がある(報告書4-5節の seed 3)。
    「44%削減」を本リポジトリの実測で裏付けたと書けない理由である。
    """
    scenario = build_scenario()
    evidence = make_evidence(
        scenario, 3, "disjoint", with_weak_axes=True, mixed_primary_axes=True,
    )
    plain = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
    aware = run_with_tiers(scenario, evidence, use_axis_error_rates=True)
    assert aware.tier2_population >= plain.tier2_population, (
        "この試行で母集団が減るようになったら、報告書4-5節を測り直す"
    )
    # 母集団に差があっても、監査件数は床のせいで同じになる。
    assert plain.audit_samples == aware.audit_samples == 5
