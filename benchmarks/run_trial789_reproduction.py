"""トライアル7・8・9の数値を、このリポジトリの実装で再現できるか測る。

なぜこれが必要だったのか
------------------------
2026-09-21 の全数点検(`docs/audit_unimplemented_trial_claims.md` 4節)で、
v8 が確立済みの前提として引用している次の3つの数値に、**本リポジトリでの
再現手段が無い**ことが分かった。

* トライアル7: 19要素を理論下限と一致する6問で解決
* トライアル8: 階層なしで精度100%→78.9%、階層ありで100%に回復(6問→8問、+33%)
* トライアル9: データ源考慮の質問選定で監査コストを44%削減

**元の合成データは Codex 側にあり、このリポジトリには残っていない。**
そのため、ここでやるのは「同じ数値を出すこと」ではなく、
**同じ性質のシナリオを組んで、この実装で何が起きるかを測ること**である。
数値が一致しない場合は、一致しないことをそのまま報告する。

シナリオ
--------
19要素・6独立クラスタ。1クラスタは部屋1つに相当し、内部は等式で結ばれる
(戸・窓・コンセント・スイッチが同数)。クラスタの大きさは 4+4+3+3+3+2 = 19。
クラスタ間は無関係なので、**理論下限は「幅の残っている連結成分の数」= 6問**
になる。この下限はハードコードせず、`DependencyGraph.components()` から
毎回計算する。

運用コストの定義(トライアル8の「監査込みの実質質問数」)
--------------------------------------------------------
v8 4-3節は「監査込みの実質質問数を運用コストとする」と書いているが、
**その内訳の定義は資料に残っていない。** ここでは次のように定義する。

    運用コスト = キラークエスチョンの質問数
               + 階層3(要確認)の要素を人が確認する回数
               + 階層2の抜き取り監査の件数

**この定義は本ベンチマークの解釈であり、トライアル8の原典と同じとは限らない。**

誤りの入れ方は2通り測る
------------------------
* **明確な矛盾** (`disjoint`): 誤った軸のレンジが正解のレンジと重ならない。
  軸間照合が矛盾を検出できる素直なケース
* **重なる誤り** (`overlapping`): 誤ったレンジが正解のレンジと部分的に重なる。
  **積集合が空にならないため矛盾として検出できず、v8 8章の穴2
  (尤もらしい誤り)になる**

実行: ``python -m benchmarks.run_trial789_reproduction``
"""

from __future__ import annotations

import argparse
import random
import statistics
from dataclasses import dataclass
from typing import Literal

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall
from arbitration.consistency_solver import ConsistencySolver
from arbitration.provisional_audit import collect_tier2_population, plan_sample_size
from killer_question.dependency_graph import build_dependency_graph
from killer_question.engine import KillerQuestionEngine
from killer_question.firewall_bridge import add_target_to_joint_solver

#: 部屋(クラスタ)名と、そこに属する要素数。合計19。
CLUSTERS: tuple[tuple[str, int], ...] = (
    ("living", 4), ("kitchen", 4), ("bath", 3),
    ("bed_a", 3), ("bed_b", 3), ("hall", 2),
)

#: 軸ごとの誤り率。v8 2章が「文章軸はトライアル9で5%と設定」と書いているので、
#: 文章軸を5%、画像軸をその6倍の30%に置く。
IMAGE_ERROR_RATE = 0.30
TEXT_ERROR_RATE = 0.05
WEAK_ERROR_RATE = 0.10

#: キラークエスチョンの同点崩しに渡すデータ源別誤り率(トライアル9のルール)。
AXIS_ERROR_RATES = {"image": IMAGE_ERROR_RATE, "text": TEXT_ERROR_RATE,
                    "history": WEAK_ERROR_RATE, "rules": WEAK_ERROR_RATE}

ErrorModel = Literal["none", "disjoint", "overlapping"]


@dataclass(frozen=True)
class Scenario:
    truth: dict[str, int]
    clusters: tuple[tuple[str, ...], ...]

    @property
    def size(self) -> int:
        return len(self.truth)


def build_scenario() -> Scenario:
    truth: dict[str, int] = {}
    clusters: list[tuple[str, ...]] = []
    for index, (room, size) in enumerate(CLUSTERS):
        members = tuple(f"{room}_{i}" for i in range(size))
        for member in members:
            truth[member] = 3 + index
        clusters.append(members)
    return Scenario(truth=truth, clusters=tuple(clusters))


def theoretical_lower_bound(scenario: Scenario) -> int:
    """理論下限の質問数を、依存グラフから計算する(ハードコードしない)。

    クラスタ内は等式で結ばれているので1問で全体が確定する。よって下限は
    「幅の残っている連結成分の数」になる。
    """
    solver = ConsistencySolver()
    for name in scenario.truth:
        solver.add_variable(name, 1, 9, axis="image", unit="count")
    _link_clusters(solver, scenario)
    result = solver.solve()
    components = build_dependency_graph(solver).components()
    return sum(
        1 for component in components
        if any(
            result.variables[n].solved_range[0] < result.variables[n].solved_range[1]
            for n in component
        )
    )


def _link_clusters(solver: ConsistencySolver, scenario: Scenario) -> None:
    """クラスタ内の等式(戸・窓・コンセントが同数)を宣言する。"""
    for members in scenario.clusters:
        present = [m for m in members if m in solver.variable_names()]
        for other in present[1:]:
            solver.add_relation(f"eq_{other}", present[0], "==", other)


def _range_for(
    value: int, *, erroneous: bool, model: ErrorModel, rng: random.Random
) -> tuple[int, int]:
    """1つの軸が出すレンジ。正解を含む幅1か、誤ったレンジか。"""
    if not erroneous or model == "none":
        return (max(1, value - 1), value + 1)
    if model == "disjoint":
        # 正解のレンジ (value-1, value+1) と重ならない位置へ飛ばす
        shift = rng.choice([-1, 1]) * 4
        low = max(1, value + shift)
        if low <= value + 1:  # 下側にずらすと重なってしまう場合は上側へ
            low = value + 3
        return (low, low + 1)
    # overlapping: 正解のレンジと1つだけ重なる(矛盾として検出できない)
    shift = rng.choice([-2, 2])
    low = max(1, value + shift)
    return (low, low + 1)


def make_evidence(
    scenario: Scenario,
    seed: int,
    model: ErrorModel,
    *,
    with_weak_axes: bool,
    mixed_primary_axes: bool = False,
) -> dict[str, list[AxisEvidence]]:
    """各要素の証拠を作る。

    ``with_weak_axes`` が True のとき、弱い軸2つを足す。**強い軸1つ+独立した
    弱い軸2つが階層2になるので、これが無いと階層2の要素が1件も生まれず、
    抜き取り監査のコストを測れない。**

    ``mixed_primary_axes`` が True のとき、クラスタごとに主軸を画像軸と文章軸で
    振り分ける。**トライアル9のルール(同じスコアなら誤り率の低いデータ源を
    優先する)は、要素の出どころが1種類しか無いと発火しようがない。** 全要素が
    画像軸だと、誤り率が全部同じになって同点崩しが変数名の昇順に落ちる
    (2026-09-21 実測)。
    """
    rng = random.Random(seed)
    cluster_of = {
        member: index
        for index, cluster in enumerate(scenario.clusters)
        for member in cluster
    }
    evidence: dict[str, list[AxisEvidence]] = {}
    for name, value in scenario.truth.items():
        items: list[AxisEvidence] = []
        if mixed_primary_axes and cluster_of[name] % 2 == 1:
            strong_axes = [("text", "spec-A", TEXT_ERROR_RATE)]
        else:
            strong_axes = [("image", "drawing-A", IMAGE_ERROR_RATE)]
        if not with_weak_axes:
            other = ("text", "spec-A", TEXT_ERROR_RATE)
            if strong_axes[0][0] == "text":
                other = ("image", "drawing-A", IMAGE_ERROR_RATE)
            strong_axes.append(other)
        for axis, source, rate in strong_axes:
            low, high = _range_for(
                value, erroneous=rng.random() < rate, model=model, rng=rng
            )
            items.append(AxisEvidence(
                derivation="read",
                target=name, count_range=(low, high), source_id=source,
                axis_id=axis, method_id=f"method_{source}", unit="count",
                calibrated=True,
            ))
        if with_weak_axes:
            for axis, source in (("history", "自社実績DB"), ("rules", "rule-A")):
                low, high = _range_for(
                    value, erroneous=rng.random() < WEAK_ERROR_RATE,
                    model=model, rng=rng,
                )
                items.append(AxisEvidence(
                    derivation="read",
                    target=name, count_range=(low, high), source_id=source,
                    axis_id=axis, method_id=f"method_{source}", unit="count",
                    strength="weak", calibrated=True,
                ))
        evidence[name] = items
    return evidence


@dataclass(frozen=True)
class Outcome:
    accuracy: float
    cost: int
    questions: int
    tier3_checks: int
    tier2_population: int
    audit_samples: int
    wrong_targets: tuple[str, ...]
    #: 矛盾(積集合が空)として検出され、人が確認した要素の数。
    #: v8 3-2節の「空集合検出で絶対ルールの誤りを発見できる」がここで効く。
    contradiction_checks: int = 0
    contradiction_rounds: int = 0
    #: 実際に質問した順序。トライアル9のルールが発火しているかの確認に使う。
    asked: tuple[str, ...] = ()


#: 矛盾解消ループの安全上限(クラスタ数より多く回る必要は無い)。
_MAX_CONTRADICTION_ROUNDS = 40


def _variables_in_conflict(solver: ConsistencySolver, names: frozenset[str]) -> set[str]:
    """矛盾の核(制約名)から、関係する変数名を取り出す。

    ``range::X`` は ``ConsistencySolver`` が変数の定義域に付ける内部名で、
    登録済みの制約ではないため ``referenced_variables()`` では引けない。
    そのため接頭辞を見て振り分ける。
    """
    found: set[str] = set()
    for name in names:
        if name.startswith("range::"):
            found.add(name[len("range::"):])
            continue
        try:
            found |= set(solver.referenced_variables(name))
        except KeyError:
            continue
    return {name for name in found if name in solver.variable_names()}


def run_without_tiers(
    scenario: Scenario, evidence: dict[str, list[AxisEvidence]]
) -> Outcome:
    """階層なし。要素ごとに積集合を採り、単一値にして**そのまま報告する**。

    エスカレーションの経路が無いのが「階層なし」の意味なので、人の確認は0回。
    積集合が空になった場合は主軸(画像軸)の読みを採る。
    """
    wrong: list[str] = []
    for name, items in evidence.items():
        low = max(item.count_range[0] for item in items)
        high = min(item.count_range[1] for item in items)
        if low > high:
            low, high = items[0].count_range
        if (low + high) // 2 != scenario.truth[name]:
            wrong.append(name)
    accuracy = 1 - len(wrong) / scenario.size
    return Outcome(accuracy, 0, 0, 0, 0, 0, tuple(sorted(wrong)))


def _build_joint_solver(
    scenario: Scenario,
    evidence: dict[str, list[AxisEvidence]],
    confirmed: dict[str, int],
) -> tuple[ConsistencySolver, dict[str, tuple[object, list[AxisEvidence]]], list[str]]:
    """ファイアウォールを通して joint solver を組み立てる。

    ``confirmed`` に入っている要素は、**軸の読みを捨てて人が確認した値で
    定義域を置き換える。** 誤った読みを残したまま等式を足すと、人の回答が
    その読み自身の定義域と矛盾して unsat のままになるため
    (実測: `range::bed_b_1` と `__human__bed_b_1` が矛盾核に並ぶ)。
    """
    firewall = AxisQualityFirewall()
    solver = ConsistencySolver()
    assessments: dict[str, tuple[object, list[AxisEvidence]]] = {}
    tier3: list[str] = []

    for name, items in evidence.items():
        if name in confirmed:
            value = confirmed[name]
            solver.add_variable(name, value, value, axis="human_confirmed", unit="count")
            continue
        decision = firewall.assess(items)
        assessments[name] = (decision, items)
        registered = add_target_to_joint_solver(solver, name, decision, items)
        if not registered.registered:
            # 階層3で solver に入らない要素。人が必ず1件ずつ確認する。
            tier3.append(name)
    _link_clusters(solver, scenario)
    return solver, assessments, tier3


def run_with_tiers(
    scenario: Scenario,
    evidence: dict[str, list[AxisEvidence]],
    *,
    use_axis_error_rates: bool,
) -> Outcome:
    """3段階確信度階層+矛盾解消+キラークエスチョン+抜き取り監査を通す。"""
    confirmed: dict[str, int] = {}
    contradiction_checks = 0
    rounds = 0

    # --- 矛盾(積集合が空)の解消 -----------------------------------------
    # クラスタ内の等式と誤った読みが両立しないとき Z3 は unsat を返す。
    # **これは検出成功であって失敗ではない**(v8 3-2節)。矛盾の核に入っている
    # 要素を人に確認して読みを置き換え、解消するまで繰り返す。
    while rounds < _MAX_CONTRADICTION_ROUNDS:
        solver, assessments, tier3 = _build_joint_solver(scenario, evidence, confirmed)
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
    else:  # pragma: no cover - 安全上限に当たった場合
        solver, assessments, tier3 = _build_joint_solver(scenario, evidence, confirmed)

    engine = KillerQuestionEngine(
        solver,
        axis_error_rates=AXIS_ERROR_RATES if use_axis_error_rates else None,
    )
    session = engine.run(lambda question: scenario.truth[question.variable])
    result = session.final_result

    wrong: list[str] = []
    for name, value in scenario.truth.items():
        if name in tier3 or name in confirmed:
            continue  # 人が確認するので正解になる
        solution = result.variables.get(name)
        if solution is None or solution.solved_range != (value, value):
            wrong.append(name)

    population = collect_tier2_population(assessments)
    # 人が確認済みにした要素(質問への回答・矛盾の解消)は監査の母集団から外す。
    answered = {answer.variable for answer in session.answered} | set(confirmed)
    remaining = tuple(c for c in population if c.target not in answered)
    audit_samples = plan_sample_size(len(remaining))

    cost = session.question_count + len(tier3) + audit_samples + contradiction_checks
    accuracy = 1 - len(wrong) / scenario.size
    return Outcome(
        accuracy=accuracy, cost=cost, questions=session.question_count,
        tier3_checks=len(tier3), tier2_population=len(remaining),
        audit_samples=audit_samples, wrong_targets=tuple(sorted(wrong)),
        contradiction_checks=contradiction_checks, contradiction_rounds=rounds,
        asked=tuple(answer.variable for answer in session.answered),
    )


# =====================================================================
# 各トライアルの測定
# =====================================================================


def report_trial7(scenario: Scenario) -> None:
    print("=" * 72)
    print("トライアル7: 19要素を理論下限と一致する質問数で解決できるか")
    print("=" * 72)
    print()
    lower_bound = theoretical_lower_bound(scenario)
    evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=False)
    outcome = run_with_tiers(scenario, evidence, use_axis_error_rates=False)

    print(f"要素数              : {scenario.size}")
    print(f"独立クラスタ数      : {len(scenario.clusters)}")
    print(f"理論下限(計算値)    : {lower_bound}問")
    print(f"実際の質問数        : {outcome.questions}問")
    print(f"精度                : {outcome.accuracy:.1%}")
    print()
    verdict = "一致した" if outcome.questions == lower_bound else "一致しなかった"
    print(f"→ 理論下限と{verdict}。")
    print()


def report_trial8(scenario: Scenario, trials: int) -> None:
    print("=" * 72)
    print("トライアル8: 誤り混入時に、階層が精度を回復させるか / コストの増分")
    print("=" * 72)
    print()
    ideal_evidence = make_evidence(scenario, seed=0, model="none", with_weak_axes=True)
    ideal = run_with_tiers(scenario, ideal_evidence, use_axis_error_rates=False)
    print(f"理想条件(誤り0%)のコスト: {ideal.cost}"
          f"(質問{ideal.questions} + 階層3 {ideal.tier3_checks} + 監査{ideal.audit_samples})")
    print()

    for model in ("disjoint", "overlapping"):
        print(f"--- 誤りの入れ方: {model} ---")
        print(f"{'seed':>4} {'階層なし':>9} {'階層あり':>9} {'コスト':>6} "
              f"{'質問':>4} {'矛盾':>4} {'階層3':>5} {'階層2':>5} {'監査':>4}")
        rows = []
        for seed in range(trials):
            evidence = make_evidence(scenario, seed, model, with_weak_axes=True)  # type: ignore[arg-type]
            flat = run_without_tiers(scenario, evidence)
            tiered = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
            rows.append((flat.accuracy, tiered.accuracy, tiered.cost))
            if seed < 10:
                print(f"{seed:>4} {flat.accuracy:>8.1%} {tiered.accuracy:>8.1%} "
                      f"{tiered.cost:>6} {tiered.questions:>4} "
                      f"{tiered.contradiction_checks:>4} {tiered.tier3_checks:>5} "
                      f"{tiered.tier2_population:>5} {tiered.audit_samples:>4}")
        flat_mean = statistics.mean(r[0] for r in rows)
        tiered_mean = statistics.mean(r[1] for r in rows)
        cost_mean = statistics.mean(r[2] for r in rows)
        perfect = sum(1 for r in rows if r[1] == 1.0)
        print()
        print(f"  {trials}試行の平均: 階層なし {flat_mean:.1%} → 階層あり {tiered_mean:.1%}")
        print(f"  階層ありが100%になった回数: {perfect}/{trials}")
        print(f"  平均コスト: {cost_mean:.1f}(理想条件 {ideal.cost} の "
              f"{cost_mean / ideal.cost:.2f}倍)")
        print()


def report_trial9(scenario: Scenario, trials: int) -> None:
    print("=" * 72)
    print("トライアル9: データ源考慮の質問選定で、監査コストが下がるか")
    print("=" * 72)
    print()
    print(f"{'seed':>4} {'考慮なし監査':>12} {'考慮あり監査':>12} "
          f"{'考慮なし総コスト':>15} {'考慮あり総コスト':>15}")
    without_audit, with_audit = [], []
    without_cost, with_cost = [], []
    for seed in range(trials):
        # **主軸をクラスタごとに振り分ける。** 全要素が同じ軸だと誤り率が
        # 全部同じになり、このルールは発火しようがない(4節)。
        evidence = make_evidence(
            scenario, seed, "disjoint", with_weak_axes=True, mixed_primary_axes=True,
        )
        plain = run_with_tiers(scenario, evidence, use_axis_error_rates=False)
        aware = run_with_tiers(scenario, evidence, use_axis_error_rates=True)
        without_audit.append(plain.audit_samples)
        with_audit.append(aware.audit_samples)
        without_cost.append(plain.cost)
        with_cost.append(aware.cost)
        if seed < 10:
            print(f"{seed:>4} {plain.audit_samples:>12} {aware.audit_samples:>12} "
                  f"{plain.cost:>15} {aware.cost:>15}")
    a0, a1 = statistics.mean(without_audit), statistics.mean(with_audit)
    c0, c1 = statistics.mean(without_cost), statistics.mean(with_cost)
    print()
    print(f"  {trials}試行の平均監査件数: 考慮なし {a0:.2f} → 考慮あり {a1:.2f}")
    if a0 > 0:
        print(f"  監査コストの削減率: {(a0 - a1) / a0:+.1%}")
    else:
        print("  **監査件数が0のため、削減率を計算できない。**")
    print(f"  {trials}試行の平均総コスト: 考慮なし {c0:.2f} → 考慮あり {c1:.2f}")
    if c0 > 0:
        print(f"  総コストの削減率: {(c0 - c1) / c0:+.1%}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()

    scenario = build_scenario()
    report_trial7(scenario)
    report_trial8(scenario, args.trials)
    report_trial9(scenario, args.trials)


if __name__ == "__main__":
    main()
