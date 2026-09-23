"""58周目: **問いの質 ― 嘘の答えを入れたら、誰か気づくか。**

基準は `docs/d_question_quality_criteria.md`(測る前にコミット済み)。

おーちゃんの指摘から起きた周。**いまの選び方は「効き」しか見ていない**ので、
「その値をそのまま教えてください」という問いが最強になる。
**分かれ目は、答えが返ってきたあとに検算されるかどうか。**

**合成案件だけで測る(取り決め④)。製品コードは 1 行も変えない。**

使い方::

    .venv/bin/python benchmarks/measure_question_quality.py

**出すのは件数と割合だけ。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchmarks.run_killer_question_eval import (  # noqa: E402
    chain_scenario,
    confluence_scenario,
    star_scenario,
    two_independent_clusters_scenario,
)
from killer_question.engine import KillerQuestionEngine, Question  # noqa: E402

SCENARIOS = {
    "星形": star_scenario,
    "2つのまとまり": two_independent_clusters_scenario,
    "鎖": chain_scenario,
    "合流": confluence_scenario,
}

#: 嘘の作り方。**基準に先に書いたとおり。線は +1 のほうで引く。**
LIE_OFFSETS = (1, 5)


def truthful(ground_truth: dict[str, int]):
    def answer_fn(question: Question) -> int:
        return ground_truth[question.variable]

    return answer_fn


def lying_about(ground_truth: dict[str, int], variable: str, offset: int):
    def answer_fn(question: Question) -> int:
        value = ground_truth[question.variable]
        return value + offset if question.variable == variable else value

    return answer_fn


def run_session(builder, answer_fn) -> dict:
    """1 回の通し。**気づかれたかどうかを、基準に書いた 3 つで判定する。**"""
    solver, _ = builder()
    engine = KillerQuestionEngine(solver)
    try:
        session = engine.run(answer_fn)
    except ValueError as error:
        # 候補に無い答えは、その場で突き返される。**これも「気づかれた」。**
        return {"noticed": True, "how": "候補に無い答えとして突き返された",
                "detail": str(error)[:40], "questions": None}
    result = solver.solve()
    if not result.is_consistent:
        return {"noticed": True, "how": "解けなくなった(矛盾)",
                "questions": session.question_count}
    return {"noticed": False, "how": "そのまま通った",
            "questions": session.question_count,
            "unresolved": len(session.remaining_unresolved)}


def main() -> int:
    print("=== E0 正しく答えたときの通し(対照1・2)===", flush=True)
    truth_runs: dict[str, dict] = {}
    asked: dict[str, tuple[str, ...]] = {}
    for name, builder in SCENARIOS.items():
        solver, ground_truth = builder()
        engine = KillerQuestionEngine(solver)
        session = engine.run(truthful(ground_truth))
        result = solver.solve()
        asked[name] = tuple(a.variable for a in session.answered)
        truth_runs[name] = {
            "questions": session.question_count,
            "consistent": result.is_consistent,
            "stopped": session.stopped_reason,
        }
        print(f"   [{name}] 問い {session.question_count} 件 / "
              f"矛盾なし: {'はい' if result.is_consistent else 'いいえ'}")

    control1 = all(run["consistent"] for run in truth_runs.values())
    control2 = sum(len(v) for v in asked.values()) >= 1
    print(f"\n対照1 正しい答えだけで矛盾が出ないか: {'はい' if control1 else 'いいえ'}")
    print(f"対照2 問いが 1 問以上あるか: "
          f"{sum(len(v) for v in asked.values())} 問 → {'はい' if control2 else 'いいえ'}")

    if not (control1 and control2):
        print("\n**対照が通らなかった。結論を出さない。**")
        return 1

    print("\n=== E1〜E4 嘘を 1 問だけ入れる ===", flush=True)
    per_offset: dict[int, dict[str, int]] = {}
    details: list[dict] = []
    for offset in LIE_OFFSETS:
        noticed = 0
        missed = 0
        print(f"\n   --- 嘘の幅: 正解 + {offset} ---")
        for name, builder in SCENARIOS.items():
            _, ground_truth = builder()
            for variable in asked[name]:
                outcome = run_session(
                    builder, lying_about(ground_truth, variable, offset)
                )
                if outcome["noticed"]:
                    noticed += 1
                else:
                    missed += 1
                details.append({"scenario": name, "variable": variable,
                                "offset": offset, **outcome})
                mark = "気づかれた" if outcome["noticed"] else "**素通り**"
                print(f"     [{name}] {variable}: {mark}({outcome['how']})")
        per_offset[offset] = {"noticed": noticed, "missed": missed}
        total = noticed + missed
        print(f"   → 気づかれた {noticed} / 素通り {missed} / 全 {total}")

    e1 = per_offset[1]["noticed"] + per_offset[1]["missed"]
    e2 = per_offset[1]["noticed"]
    e3 = per_offset[1]["missed"]
    ratio = e3 / e1 if e1 else 0.0

    print("\n=== 対照3 反復 3 回(E3)===", flush=True)
    repeats = []
    for _ in range(2):
        missed = 0
        for name, builder in SCENARIOS.items():
            _, ground_truth = builder()
            for variable in asked[name]:
                if not run_session(builder, lying_about(ground_truth, variable, 1))["noticed"]:
                    missed += 1
        repeats.append(missed)
    repeats.append(e3)
    control3 = len(set(repeats)) == 1
    print(f"   {repeats} → {'はい' if control3 else 'いいえ'}")

    print("\n=== 採否(基準に先に書いた線) ===")
    if ratio >= 0.5:
        verdict = "quality_axis_needed"
        print(f"   E3 ÷ E1 = {e3}/{e1} = {ratio:.2f} ≧ 0.5 → "
              "**半分以上の問いは、嘘をついても誰も気づかない。**")
        print("   **おーちゃんの見立てが当たっている。**質の軸を足す設計を出す"
              "(設計まで。実装は PR のまま待機)。")
    elif e3 == 0:
        verdict = "all_checked"
        print("   E3 = 0 → **全部の問いが検算される。**"
              "いまの選び方でも質は保たれている。")
    else:
        verdict = "partial"
        print(f"   0 < {ratio:.2f} < 0.5 → **割合を記録し、"
              "気づかれない問いがどの種類かを名指しする。**設計は出さない。")

    print("\n   ※ 測れていないこと[人が要る=未測定]: "
          "その問いに人が本当に答えられるか / 手間と時間 / 現場で答えが手に入るか")

    payload = {
        "round": 58,
        "criteria_file": "docs/d_question_quality_criteria.md",
        "benchmark": "benchmarks/measure_question_quality.py",
        "E0_truth_runs": truth_runs,
        "E1_tried": e1,
        "E2_noticed": e2,
        "E3_missed": e3,
        "E3_ratio": round(ratio, 4),
        "E4_by_offset": {str(k): v for k, v in per_offset.items()},
        "details": details,
        "controls": {"1_no_false_alarm": control1, "2_has_questions": control2,
                      "3_repeat": repeats},
        "verdict": verdict,
        "implementation_changed": False,
        "synthetic_only": True,
    }
    out = ROOT / "docs" / "d_question_quality_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き出し: docs/{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
