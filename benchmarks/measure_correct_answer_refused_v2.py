"""60周目: **正しい答えが突き返されることはあるのか(やり直し)。**

基準は `docs/d_correct_answer_refused_v2_criteria.md`(測る前にコミット済み `e4fb41d`)。

59 周目は**ずらす相手を問われた変数に限った**ので、等式で縛られている変数では
候補が動かず、対照が通らなかった。**今度は案件の変数を 1 つずつ順にずらす。**

**製品コードは 1 行も変えない。**

使い方::

    .venv/bin/python benchmarks/measure_correct_answer_refused_v2.py

**出すのは件数だけ。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchmarks.measure_correct_answer_refused import (  # noqa: E402
    SHIFT,
    run,
    shifted_reading,
)
from benchmarks.measure_question_quality import SCENARIOS, truthful  # noqa: E402
from killer_question.engine import KillerQuestionEngine  # noqa: E402


def asked_variables(builder, ground_truth: dict[str, int]) -> tuple[str, ...]:
    solver, _ = builder()
    engine = KillerQuestionEngine(solver)
    session = engine.run(truthful(ground_truth))
    return tuple(a.variable for a in session.answered)


def candidates_of_first_question(builder, ground_truth: dict[str, int]) -> dict:
    """出た問いごとの候補の一覧。ずらす前後で比べるために使う。"""
    solver, _ = builder()
    engine = KillerQuestionEngine(solver)
    out: dict[str, tuple] = {}
    for _ in range(20):
        question = engine.next_question()
        if question is None:
            break
        out[question.variable] = question.candidate_values
        engine.answer(question.variable, ground_truth[question.variable])
    return out


def main() -> int:
    print("=== 対照1 読みが正しいときの通し ===", flush=True)
    control1 = True
    baseline: dict[str, dict] = {}
    all_variables: dict[str, tuple[str, ...]] = {}
    for name, builder in SCENARIOS.items():
        solver, ground_truth = builder()
        all_variables[name] = tuple(solver.variable_names())
        outcome = run(builder, ground_truth)
        control1 = control1 and outcome["outcome"] == "passed"
        baseline[name] = candidates_of_first_question(builder, ground_truth)
        print(f"   [{name}] {outcome['outcome']} / 変数 {len(all_variables[name])} 個 / "
              f"問い {tuple(baseline[name])}")
    print(f"   → 突き返し 0 件か: {'はい' if control1 else 'いいえ'}")

    print("\n=== H0〜H4 変数を 1 つずつずらす ===", flush=True)
    h0 = 0
    kept: list[dict] = []
    skipped: list[dict] = []
    for name, builder in SCENARIOS.items():
        _, ground_truth = builder()
        for variable in all_variables[name]:
            h0 += 1
            with shifted_reading(variable):
                after = candidates_of_first_question(builder, ground_truth)
                outcome = run(builder, ground_truth)
            # **問われた変数の候補が現に変わった組だけを数える。**
            changed = any(
                after.get(asked) != before
                for asked, before in baseline[name].items()
            ) or set(after) != set(baseline[name])
            record = {"scenario": name, "shifted": variable, **outcome}
            if changed:
                kept.append(record)
            else:
                skipped.append(record)

    h1 = len(kept)
    h2 = sum(1 for r in kept if r["outcome"] == "refused")
    h3 = sum(1 for r in kept if r["outcome"] == "unsat")
    h4 = sum(1 for r in kept if r["outcome"] == "passed")
    other = h1 - h2 - h3 - h4
    print(f"   H0 試した組: {h0}")
    print(f"   H1 ずらしが効いた組: {h1}(捨てた組 {len(skipped)})")
    print(f"   H2 **正しい答えが突き返された**: {h2}")
    print(f"   H3 矛盾として先に止まった: {h3}")
    print(f"   H4 そのまま通った: {h4}")
    if other:
        print(f"   その他(別の失敗): {other}")
    for record in kept:
        label = {"refused": "**突き返された**", "unsat": "矛盾で止まった",
                 "passed": "そのまま通った", "error": "別の失敗"}[record["outcome"]]
        print(f"     [{record['scenario']}] {record['shifted']} をずらす → {label}")

    control2 = h1 >= 1
    print(f"\n対照2 ずらしが効いた組があるか: {'はい' if control2 else 'いいえ'}")

    repeats = [h2]
    for _ in range(2):
        refused = 0
        for name, builder in SCENARIOS.items():
            _, ground_truth = builder()
            for variable in all_variables[name]:
                with shifted_reading(variable):
                    after = candidates_of_first_question(builder, ground_truth)
                    outcome = run(builder, ground_truth)
                changed = any(
                    after.get(asked) != before
                    for asked, before in baseline[name].items()
                ) or set(after) != set(baseline[name])
                if changed and outcome["outcome"] == "refused":
                    refused += 1
        repeats.append(refused)
    control3 = len(set(repeats)) == 1
    print(f"対照3 反復 3 回(H2): {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if not (control1 and control2):
        verdict = "controls_failed"
        print("   **対照1 か対照2 が通らなかった。結論を出さない。**")
    elif h2 >= 1:
        verdict = "refusal_is_real"
        print(f"   H2 = {h2} ≧ 1 → **正しい答えが拒まれる場合が実在する。**"
              "設計4節の提案に実測の裏づけが付いた。")
    elif h3 >= 1:
        verdict = "unsat_first"
        print(f"   H2 = 0 かつ H3 = {h3} ≧ 1 → **拒む前に、矛盾として先に止まる。**"
              "**提案4は理屈だけで実測の裏づけが無い。提案を弱める。**")
    else:
        verdict = "nothing_happens"
        print("   H2 = 0 かつ H3 = 0 → **読みがずれても何も起きない。**"
              "提案4 とは別の、もっと大きな問題である。")

    print(f"\n   対照4(記録だけ): 数えた組 {h1} / 試した組 {h0} "
          f"= {h1 / h0:.2f}。**捨てた組が多いほど、この数字は狭い範囲の話になる。**")

    payload = {
        "round": 60,
        "criteria_commit": "e4fb41d",
        "criteria_file": "docs/d_correct_answer_refused_v2_criteria.md",
        "benchmark": "benchmarks/measure_correct_answer_refused_v2.py",
        "shift": SHIFT,
        "H0_tried": h0,
        "H1_effective": h1,
        "H2_refused": h2,
        "H3_unsat": h3,
        "H4_passed": h4,
        "kept": kept,
        "skipped": skipped,
        "controls": {"1_no_refusal_when_correct": control1,
                      "2_some_shift_worked": control2,
                      "3_repeat": repeats,
                      "4_kept_ratio": round(h1 / h0, 4) if h0 else None},
        "verdict": verdict,
        "implementation_changed": False,
        "synthetic_only": True,
    }
    out = ROOT / "docs" / "d_correct_answer_refused_v2_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
