"""群合計の吸収に1問を立てる (c) を、合成の群で測る(判断の5番)。

基準は `docs/group_total_question_criteria.md`(測る前にコミット済み)。
**合成データだけ。** 実図面・実見積は使わない。

使い方::

    python benchmarks/measure_group_total_question.py [出力JSONのパス]

出力の既定は `docs/group_total_question_result.json`。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arbitration.axis_quality_firewall import AxisEvidence, AxisQualityFirewall  # noqa: E402
from arbitration.consistency_solver import ConsistencySolver  # noqa: E402
from arbitration.group_total import (  # noqa: E402
    GroupTotalConstraint,
    check_group_total,
    group_total_absorption_excess,
)
from killer_question.engine import KillerQuestionEngine  # noqa: E402
from killer_question.firewall_bridge import add_target_to_joint_solver  # noqa: E402

NAMES = tuple(f"door_{i}" for i in range(10))


def _evidence(target, count_range, source_id, axis_id, *, strength="strong", calibrated=True):
    return AxisEvidence(
        target=target, count_range=count_range, source_id=source_id, axis_id=axis_id,
        method_id=f"method_{source_id}", unit="count", derivation="read",
        strength=strength, calibrated=calibrated,
    )


def _agreeing(target, value):
    return [_evidence(target, (value, value), "drawing-A", "image"),
            _evidence(target, (value, value), "spec-A", "text")]


def _stopped(target, count_range, *, calibrated=True, weak_range=None):
    return [_evidence(target, count_range, "drawing-A", "image", calibrated=calibrated),
            _evidence(target, weak_range or count_range, "自社実績DB", "history",
                      strength="weak")]


def _snapshot(solver: ConsistencySolver) -> tuple:
    names = sorted(solver.variable_names())
    return (tuple(names),
            tuple(solver.variable_range(n) for n in names),
            tuple(solver.variable_detection_range(n) for n in names),
            tuple(solver.requires_confirmation(n) for n in names),
            solver.constraint_names())


def measure_group(name, evidence, truth, *, stopped, wrong, error_kind):
    firewall = AxisQualityFirewall()
    solver = ConsistencySolver()
    tiers = {}
    for target in NAMES:
        decision = firewall.assess(evidence[target])
        tiers[target] = decision.tier
        result = add_target_to_joint_solver(solver, target, decision, evidence[target])
        if not result.registered:
            return {"name": name, "excluded": f"{target} が登録されなかった"}
    precondition = all(tiers[s] == 3 for s in stopped) and all(
        tiers[t] == 1 for t in NAMES if t not in stopped)
    if not precondition:
        return {"name": name, "excluded": "前提の階層にならなかった",
                "tiers": tiers}
    constraint = GroupTotalConstraint("group_total", NAMES, sum(truth.values()), unit="count")
    constraint.apply(solver)

    check_before = check_group_total(solver, constraint)
    snap_before = _snapshot(solver)
    excess = group_total_absorption_excess(solver, constraint)

    # 前: 既存の next_question(標準モード、この変更で触っていない)
    before_engine = KillerQuestionEngine(solver)
    before_q = before_engine.next_question()

    # 後: (c)
    engine = KillerQuestionEngine(solver)
    engine_snap = _snapshot(engine.solver)
    question = engine.group_total_question(constraint)
    unchanged = (_snapshot(solver) == snap_before
                 and _snapshot(engine.solver) == engine_snap
                 and check_group_total(solver, constraint) == check_before
                 and check_group_total(engine.solver, constraint) == check_before)

    row = {
        "name": name,
        "error_kind": error_kind,
        "planted_wrong": sorted(wrong),
        "stopped": list(stopped),
        "status": check_before.status,
        "absorbed": check_before.is_absorbed,
        "absorbing": list(check_before.absorbing_targets),
        "excess": None if excess is None else float(excess),
        "audit_before": list(check_before.targets_requiring_audit),
        "before_next_question": None if before_q is None else before_q.variable,
        "question": None if question is None else question.variable,
        "question_score": None if question is None else question.score,
        "question_grade": None if question is None else question.grade,
        "unchanged_by_question": unchanged,
    }
    if question is None:
        return row
    row["chosen_is_planted_wrong"] = question.variable in wrong
    answer = truth[question.variable]
    if answer not in question.candidate_values:
        row["after_answer"] = "disagreement"
        return row
    engine.answer(question.variable, answer)
    after = check_group_total(engine.solver, constraint)
    if after.status == "unsat":
        row["after_answer"] = "unsat_group_stops"
    elif after.is_absorbed:
        row["after_answer"] = "still_absorbed"
    else:
        row["after_answer"] = "sat_no_absorption"
    # 階層1に誤りを仕込んだのに、答えの後で群が止まらない = 誤りは隠れたまま
    tier1_wrong = [t for t in wrong if t not in stopped]
    row["tier1_error_hidden_after_answer"] = bool(tier1_wrong) and after.status != "unsat"
    return row


def named_scenarios():
    truth = {n: 3 for n in NAMES}
    rows = []

    def base(stopped_ev, wrong):
        ev = {n: _agreeing(n, wrong.get(n, truth[n])) for n in NAMES}
        ev["door_0"] = stopped_ev
        return ev

    specs = [
        ("N1 誤りなし・停止なし", _agreeing("door_0", 3), {}, "none", ()),
        ("N2 誤りなし・停止(幅広)", _stopped("door_0", (0, 6)), {}, "none", ("door_0",)),
        ("N3 誤りなし・停止(幅狭)", _stopped("door_0", (2, 4)), {}, "none", ("door_0",)),
        ("N4 誤りなし・強い軸0", _stopped("door_0", (2, 4), calibrated=False, weak_range=(1, 9)),
         {}, "none", ("door_0",)),
        ("E1 階層1が−2", _stopped("door_0", (0, 6)), {"door_1": 1}, "tier1", ("door_0",)),
        ("E2 階層1が−2×2", _stopped("door_0", (0, 6)), {"door_1": 1, "door_2": 1}, "tier1",
         ("door_0",)),
        ("E3 階層1が+1(死角)", _stopped("door_0", (0, 6)), {"door_1": 4}, "tier1", ("door_0",)),
        ("E4 階層1が−2・強い軸0", _stopped("door_0", (0, 6), calibrated=False), {"door_1": 1},
         "tier1", ("door_0",)),
        ("E5 停止した要素の読みがずれ", _stopped("door_0", (2, 10)), {}, "stopped_center",
         ("door_0",)),
    ]
    for name, stopped_ev, wrong, kind, stopped in specs:
        planted = set(wrong) | ({"door_0"} if kind == "stopped_center" else set())
        rows.append(measure_group(name, base(stopped_ev, wrong), truth, stopped=stopped,
                                  wrong=planted, error_kind=kind))
    ev = base(_stopped("door_0", (0, 6)), {"door_1": 1, "door_2": 1, "door_3": 1})
    ev["door_5"] = _stopped("door_5", (0, 6))
    rows.append(measure_group("E6 停止2つ・階層1が−2×3", ev, truth, stopped=("door_0", "door_5"),
                              wrong={"door_1", "door_2", "door_3"}, error_kind="tier1"))
    return rows


GRID_TRUTH = dict(zip(NAMES, [3, 4, 2, 5, 3, 4, 2, 3, 4, 3]))
STOP_ORDER = ("door_0", "door_5", "door_9")
DELTAS = (-3, -2, -1, 1, 2, 3)


def grid_scenarios():
    rows = []
    for k in (1, 2, 3):
        stopped = STOP_ORDER[:k]
        for w in (1, 2, 3):
            errors = [("none", 0)] + [("tier1", d) for d in DELTAS] + [
                ("stopped_center", d) for d in DELTAS]
            for kind, d in errors:
                ev = {}
                wrong: set[str] = set()
                for n in NAMES:
                    t = GRID_TRUTH[n]
                    if n in stopped:
                        center = t + (d if (kind == "stopped_center" and n == "door_0") else 0)
                        ev[n] = _stopped(n, (max(0, center - w), center + w))
                    elif kind == "tier1" and n == "door_1":
                        ev[n] = _agreeing(n, t + d)
                    else:
                        ev[n] = _agreeing(n, t)
                if kind == "tier1":
                    wrong = {"door_1"}
                elif kind == "stopped_center":
                    wrong = {"door_0"}
                name = f"k={k} w={w} {kind} d={d:+d}"
                row = measure_group(name, ev, GRID_TRUTH, stopped=stopped, wrong=wrong,
                                    error_kind=kind)
                row.update({"k": k, "w": w, "d": d})
                rows.append(row)
    return rows


def summarize(rows):
    measured = [r for r in rows if "excluded" not in r]
    absorbed = [r for r in measured if r["absorbed"]]
    not_absorbed = [r for r in measured if not r["absorbed"]]
    negative = [r for r in measured if r["error_kind"] == "none"]
    unsat = [r for r in measured if r["status"] == "unsat"]
    asked = [r for r in measured if r["question"] is not None]
    by_kind = {}
    for kind in ("none", "tier1", "stopped_center"):
        rs = [r for r in measured if r["error_kind"] == kind]
        qs = [r for r in rs if r["question"] is not None]
        by_kind[kind] = {
            "groups": len(rs),
            "unsat": sum(r["status"] == "unsat" for r in rs),
            "absorbed": sum(r["absorbed"] for r in rs),
            "sat_not_absorbed": sum(r["status"] == "sat" and not r["absorbed"] for r in rs),
            "questions_after": len(qs),
            "chosen_is_planted_wrong": sum(bool(r.get("chosen_is_planted_wrong")) for r in qs),
            "after_answer": dict(Counter(r.get("after_answer") for r in qs)),
            "tier1_error_hidden_after_answer": sum(
                bool(r.get("tier1_error_hidden_after_answer")) for r in qs),
        }
    return {
        "groups_total": len(rows),
        "excluded": len(rows) - len(measured),
        "measured": len(measured),
        "questions_before_group_path": 0,
        "questions_after_group_path": len(asked),
        "before_next_question_non_none": sum(r["before_next_question"] is not None
                                             for r in measured),
        "before_next_question_non_none_on_absorbed": sum(
            r["before_next_question"] is not None for r in absorbed),
        "A1_negative_control_questions": sum(r["question"] is not None for r in negative),
        "A1_negative_control_groups": len(negative),
        "A2_not_absorbed_questions": sum(r["question"] is not None for r in not_absorbed),
        "A2_not_absorbed_groups": len(not_absorbed),
        "A3_absorbed_groups": len(absorbed),
        "A3_absorbed_with_exactly_one_question": sum(r["question"] is not None
                                                     for r in absorbed),
        "A4_unchanged_all": all(r["unchanged_by_question"] for r in measured),
        "A4_changed_count": sum(not r["unchanged_by_question"] for r in measured),
        "A5_unsat_groups": len(unsat),
        "A5_unsat_all_members_audited": sum(len(r["audit_before"]) == len(NAMES)
                                            for r in unsat),
        "by_error_kind": by_kind,
    }


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/group_total_question_result.json"
    named = named_scenarios()
    grid = grid_scenarios()
    result = {
        "criteria": "docs/group_total_question_criteria.md",
        "synthetic_only": True,
        "named": {"summary": summarize(named), "rows": named},
        "grid": {"summary": summarize(grid), "rows": grid},
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"named": result["named"]["summary"], "grid": result["grid"]["summary"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
