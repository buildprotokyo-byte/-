"""59周目: **正しい答えが突き返されることはあるのか。**

基準は `docs/d_correct_answer_refused_criteria.md`(測る前にコミット済み `f1857ea`)。

58 周目の設計 4 節に**理屈で書いたこと**を、自分で確かめる。
**確かめた結果、起こらなければ提案を弱める。**

**製品コードは 1 行も変えない。** 読みの幅をずらすのは、このファイルの中で
`ConsistencySolver.add_variable` を一時的に包むだけで行う(合成案件のみ)。

使い方::

    .venv/bin/python benchmarks/measure_correct_answer_refused.py

**出すのは件数だけ。**
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arbitration.consistency_solver import ConsistencySolver  # noqa: E402
from benchmarks.measure_question_quality import SCENARIOS, truthful  # noqa: E402
from killer_question.engine import KillerQuestionEngine  # noqa: E402

#: 読みのずらし幅。**基準に先に書いたとおり。**
SHIFT = 2


@contextlib.contextmanager
def shifted_reading(variable: str, shift: int = SHIFT):
    """`variable` の**読みの幅だけ**をまるごとずらす。正解の値は動かさない。

    **製品コードは変えない。**このかたまりの中にいるあいだだけ包む。
    """
    original = ConsistencySolver.add_variable

    def patched(self, name, lower, upper, **kwargs):  # type: ignore[no-untyped-def]
        if name == variable:
            lower, upper = lower + shift, upper + shift
        return original(self, name, lower, upper, **kwargs)

    ConsistencySolver.add_variable = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        ConsistencySolver.add_variable = original  # type: ignore[assignment]


def run(builder, ground_truth: dict[str, int]) -> dict:
    solver, _ = builder()
    engine = KillerQuestionEngine(solver)
    try:
        session = engine.run(truthful(ground_truth))
    except ValueError as error:
        return {"outcome": "refused", "detail": str(error)[:60]}
    except Exception as error:  # noqa: BLE001 - 何が起きたかを残したい
        return {"outcome": "error", "detail": f"{type(error).__name__}: {error}"[:60]}
    result = solver.solve()
    if not result.is_consistent:
        return {"outcome": "unsat", "questions": session.question_count}
    return {"outcome": "passed", "questions": session.question_count}


def candidates_for(builder, ground_truth: dict[str, int], variable: str) -> tuple | None:
    """その変数について、仕組みが出す候補の一覧(対照2 に使う)。"""
    solver, _ = builder()
    engine = KillerQuestionEngine(solver)
    for _ in range(20):
        question = engine.next_question()
        if question is None:
            return None
        if question.variable == variable:
            return question.candidate_values
        engine.answer(question.variable, ground_truth[question.variable])
    return None


def main() -> int:
    print("=== 対照1 読みが正しいときの通し ===", flush=True)
    asked: dict[str, tuple[str, ...]] = {}
    control1 = True
    for name, builder in SCENARIOS.items():
        _, ground_truth = builder()
        outcome = run(builder, ground_truth)
        solver, _ = builder()
        engine = KillerQuestionEngine(solver)
        session = engine.run(truthful(ground_truth))
        asked[name] = tuple(a.variable for a in session.answered)
        ok = outcome["outcome"] == "passed"
        control1 = control1 and ok
        print(f"   [{name}] {outcome['outcome']} / 問い {asked[name]}")
    print(f"   → 正しい答えが突き返された問い 0 件か: {'はい' if control1 else 'いいえ'}")

    print("\n=== 対照2 ずらしが効いているか ===", flush=True)
    control2 = True
    control2_detail: list[dict] = []
    for name, builder in SCENARIOS.items():
        _, ground_truth = builder()
        for variable in asked[name]:
            before = candidates_for(builder, ground_truth, variable)
            with shifted_reading(variable):
                after = candidates_for(builder, ground_truth, variable)
            changed = before != after
            control2 = control2 and changed
            control2_detail.append({"scenario": name, "variable": variable,
                                     "candidates_changed": changed})
            print(f"   [{name}] {variable}: 候補が変わったか: {'はい' if changed else 'いいえ'}")

    if not (control1 and control2):
        print("\n**対照が通らなかった。結論を出さない。**")
        # 止まったことも記録に残す(何がどう通らなかったかを後から読めるように)。
        held = {
            "round": 59,
            "criteria_commit": "f1857ea",
            "criteria_file": "docs/d_correct_answer_refused_criteria.md",
            "benchmark": "benchmarks/measure_correct_answer_refused.py",
            "shift": SHIFT,
            "controls": {
                "1_no_refusal_when_correct": control1,
                "2_shift_changes_candidates": control2,
                "2_per_variable": control2_detail,
            },
            "verdict": "controls_failed",
            "implementation_changed": False,
            "synthetic_only": True,
        }
        out = ROOT / "docs" / "d_correct_answer_refused_result.json"
        out.write_text(json.dumps(held, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"書き出し: docs/{out.name}")
        return 1

    print("\n=== G1〜G3 読みをずらして、正しい答えを入れる ===", flush=True)
    counts = {"refused": 0, "unsat": 0, "passed": 0, "error": 0}
    details: list[dict] = []
    for name, builder in SCENARIOS.items():
        _, ground_truth = builder()
        for variable in asked[name]:
            with shifted_reading(variable):
                outcome = run(builder, ground_truth)
            counts[outcome["outcome"]] += 1
            details.append({"scenario": name, "variable": variable, **outcome})
            label = {
                "refused": "**正しい答えが突き返された**",
                "unsat": "矛盾として先に止まった",
                "passed": "そのまま通った",
                "error": "別の失敗",
            }[outcome["outcome"]]
            print(f"   [{name}] {variable}: {label}")

    g1, g2, g3 = counts["refused"], counts["unsat"], counts["passed"]
    total = sum(counts.values())
    print(f"\n   G1 突き返された: {g1} / G2 矛盾で止まった: {g2} / "
          f"G3 そのまま通った: {g3} / 全 {total}")

    print("\n=== 対照3 反復 3 回(G1)===", flush=True)
    repeats = []
    for _ in range(2):
        refused = 0
        for name, builder in SCENARIOS.items():
            _, ground_truth = builder()
            for variable in asked[name]:
                with shifted_reading(variable):
                    if run(builder, ground_truth)["outcome"] == "refused":
                        refused += 1
        repeats.append(refused)
    repeats.append(g1)
    control3 = len(set(repeats)) == 1
    print(f"   {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if g1 >= 1:
        verdict = "refusal_is_real"
        print(f"   G1 = {g1} ≧ 1 → **正しい答えが拒まれる場合が実在する。**"
              "設計4節の提案に実測の裏づけが付いた。")
    elif g2 >= 1:
        verdict = "unsat_first"
        print(f"   G1 = 0 かつ G2 = {g2} ≧ 1 → **拒む前に、矛盾として先に止まる。**"
              "**提案4は理屈だけで、実測の裏づけは無い。提案を弱める。**")
    else:
        verdict = "nothing_happens"
        print("   G1 = 0 かつ G2 = 0 → **読みがずれても何も起きない。**"
              "これは提案4とは別の、もっと大きな問題である。")

    payload = {
        "round": 59,
        "criteria_commit": "f1857ea",
        "criteria_file": "docs/d_correct_answer_refused_criteria.md",
        "benchmark": "benchmarks/measure_correct_answer_refused.py",
        "shift": SHIFT,
        "G1_refused": g1,
        "G2_unsat": g2,
        "G3_passed": g3,
        "G_error": counts["error"],
        "total": total,
        "details": details,
        "controls": {"1_no_refusal_when_correct": control1,
                      "2_shift_changes_candidates": control2,
                      "3_repeat": repeats},
        "verdict": verdict,
        "implementation_changed": False,
        "synthetic_only": True,
    }
    out = ROOT / "docs" / "d_correct_answer_refused_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
