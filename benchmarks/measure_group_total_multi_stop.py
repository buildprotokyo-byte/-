"""停止した要素が2つ以上ある群で、群合計の誤りが素通りする件を測る(K-07 の6番)。

基準は `docs/group_total_multi_stop_criteria.md`(測る前にコミット済み)。
**合成データだけ。** 実図面・実見積は使わない。**判定のコードは変えない。**

いまの判定(`arbitration.group_total.check_group_total`)と、このスクリプトの中にだけ
置いた直し方の候補(:func:`candidate_absorbed`。設計書 4-1節の式)を、同じ群で並べて測る。

使い方::

    python benchmarks/measure_group_total_multi_stop.py [出力JSONのパス]

出力の既定は `docs/group_total_multi_stop_result.json`。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arbitration.axis_quality_firewall import AxisQualityFirewall  # noqa: E402
from arbitration.consistency_solver import ConsistencySolver  # noqa: E402
from arbitration.group_total import (  # noqa: E402
    GroupTotalCheck,
    GroupTotalConstraint,
    _center_window,
    check_group_total,
)
from benchmarks.measure_group_total_question import (  # noqa: E402
    GRID_TRUTH,
    NAMES,
    _agreeing,
    _stopped,
)
from killer_question.firewall_bridge import add_target_to_joint_solver  # noqa: E402

STOP_ORDER = ("door_0", "door_5", "door_9", "door_7")
STOP_COUNTS = (0, 1, 2, 3, 4)
HALF_WIDTHS = (1, 2, 3)
TIER1_TARGET = "door_3"  # 正解5。±5 まで入れても読みが負にならない
TIER1_DELTAS = (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5)
STOPPED_TARGET = "door_0"
STOPPED_DELTAS = (-3, -2, -1, 1, 2, 3)
AUTO_CONFIRMED_TIER = 1
#: 個数の許容差(`CENTER_TOLERANCES["count"]`)。「群の許容幅」= これ × 停止要素数
COUNT_TOLERANCE = 1


def build_group(k: int, w: int, kind: str, d: int):
    """格子の1群を、本番と同じ組み方で solver に登録する。

    戻り値は ``(solver, constraint, stopped, tiers)``。前提の階層にならなかったら
    ``solver`` は ``None``。
    """
    stopped = STOP_ORDER[:k]
    evidence = {}
    for n in NAMES:
        t = GRID_TRUTH[n]
        if n in stopped:
            center = t + (d if (kind == "stopped" and n == STOPPED_TARGET) else 0)
            evidence[n] = _stopped(n, (max(0, center - w), center + w))
        elif kind == "tier1" and n == TIER1_TARGET:
            evidence[n] = _agreeing(n, t + d)
        else:
            evidence[n] = _agreeing(n, t)

    firewall = AxisQualityFirewall()
    solver = ConsistencySolver()
    tiers = {}
    for n in NAMES:
        decision = firewall.assess(evidence[n])
        tiers[n] = decision.tier
        result = add_target_to_joint_solver(solver, n, decision, evidence[n])
        if not result.registered:
            return None, None, stopped, tiers
    ok = all(tiers[s] == 3 for s in stopped) and all(
        tiers[n] == 1 for n in NAMES if n not in stopped)
    if not ok:
        return None, None, stopped, tiers
    constraint = GroupTotalConstraint(
        "group_total", NAMES, sum(GRID_TRUTH.values()), unit="count")
    constraint.apply(solver)
    return solver, constraint, stopped, tiers


def group_residual_window(solver: ConsistencySolver, constraint: GroupTotalConstraint):
    """設計書 4-1節の区間演算。

    ``R`` = 停止した要素が担うべき合計の区間(群合計 − 停止していない要素の
    検出用の範囲の合計)、``window`` = 停止した要素の中心窓(`check_group_total` と
    同じ ``_center_window``)の合計。停止した要素が無ければ ``None``。
    """
    stopped = [n for n in constraint.members if solver.requires_confirmation(n)]
    if not stopped:
        return None
    fixed = [solver.variable_detection_range(n)
             for n in constraint.members if n not in stopped]
    r_low = Fraction(constraint.total - sum(high for _, high in fixed))
    r_high = Fraction(constraint.total - sum(low for low, _ in fixed))
    w_low = Fraction(0)
    w_high = Fraction(0)
    for n in stopped:
        low, high = _center_window(solver.variable_detection_range(n), constraint.unit)
        w_low += low
        w_high += high
    return (r_low, r_high), (w_low, w_high)


def candidate_absorbed(
    solver: ConsistencySolver, constraint: GroupTotalConstraint, current: GroupTotalCheck
) -> tuple[bool, tuple[str, ...], bool]:
    """**直し方の候補(このスクリプトの中だけ)。判定のコードは変えていない。**

    いまの判定が吸収ならそのまま吸収。そうでなくても、群が sat で、残差 ``R`` が
    停止した要素の窓の合計と交わらなければ吸収とする。unsat には触らない。

    戻り値は ``(吸収か, targets_requiring_audit, 群の式だけが吸収とした か)``。
    """
    if current.status == "unsat":
        return False, current.targets_requiring_audit, False
    if current.is_absorbed:
        return True, current.targets_requiring_audit, False
    rw = group_residual_window(solver, constraint)
    if rw is None:
        return False, current.targets_requiring_audit, False
    (r_low, r_high), (w_low, w_high) = rw
    if r_high < w_low or r_low > w_high:
        audit = tuple(
            n for n in constraint.members
            if not solver.requires_confirmation(n)
            and solver.variable_evidence(n).get("firewall_tier") == AUTO_CONFIRMED_TIER
        )
        return True, audit, True
    return False, current.targets_requiring_audit, False


def _verdict(status: str, absorbed: bool) -> str:
    if status == "unsat":
        return "unsat"
    return "absorbed" if absorbed else "sat_not_absorbed"


def measure_one(k: int, w: int, kind: str, d: int) -> dict:
    name = f"k={k} w={w} {kind} d={d:+d}"
    solver, constraint, stopped, tiers = build_group(k, w, kind, d)
    row = {"name": name, "k": k, "w": w, "kind": kind, "d": d, "stopped": list(stopped)}
    if solver is None:
        row["excluded"] = "前提の階層にならなかった"
        row["tiers"] = tiers
        return row
    current = check_group_total(solver, constraint)
    cand_absorbed, cand_audit, by_group_formula = candidate_absorbed(
        solver, constraint, current)
    rw = group_residual_window(solver, constraint)
    row.update({
        "current": _verdict(current.status, current.is_absorbed),
        "current_absorbing": list(current.absorbing_targets),
        "current_audit": list(current.targets_requiring_audit),
        "candidate": _verdict(current.status, cand_absorbed),
        "candidate_audit": list(cand_audit),
        "candidate_by_group_formula_only": by_group_formula,
        "audit_changed": tuple(cand_audit) != current.targets_requiring_audit,
        "residual": None if rw is None else [float(x) for x in rw[0]],
        "stopped_window_sum": None if rw is None else [float(x) for x in rw[1]],
    })
    if kind == "tier1":
        size = abs(d)
        if size <= COUNT_TOLERANCE:
            band = "within_element_tolerance"
        elif size <= COUNT_TOLERANCE * k:
            band = "within_group_tolerance"
        else:
            band = "beyond_group_tolerance"
        row["band"] = band
        row["current_slip"] = band != "within_element_tolerance" and row["current"] == "sat_not_absorbed"
        row["candidate_slip"] = (band != "within_element_tolerance"
                                 and row["candidate"] == "sat_not_absorbed")
    return row


def grid_rows() -> list[dict]:
    rows = []
    for k in STOP_COUNTS:
        widths = HALF_WIDTHS if k > 0 else (0,)
        for w in widths:
            specs = [("none", 0)] + [("tier1", d) for d in TIER1_DELTAS]
            if k > 0:
                specs += [("stopped", d) for d in STOPPED_DELTAS]
            for kind, d in specs:
                rows.append(measure_one(k, w, kind, d))
    return rows


def _slip_table(rows: list[dict]) -> list[dict]:
    """k × |d| ごとの、階層1の誤りの判定の内訳(いま / 候補)。"""
    table = []
    for k in STOP_COUNTS:
        for size in sorted({abs(d) for d in TIER1_DELTAS}):
            rs = [r for r in rows if r["kind"] == "tier1" and r["k"] == k
                  and abs(r["d"]) == size and "excluded" not in r]
            if not rs:
                continue
            table.append({
                "k": k, "abs_d": size, "band": rs[0]["band"], "groups": len(rs),
                "current": dict(Counter(r["current"] for r in rs)),
                "candidate": dict(Counter(r["candidate"] for r in rs)),
                "current_slip": sum(r["current_slip"] for r in rs),
                "candidate_slip": sum(r["candidate_slip"] for r in rs),
            })
    return table


def summarize(rows: list[dict]) -> dict:
    measured = [r for r in rows if "excluded" not in r]
    tier1 = [r for r in measured if r["kind"] == "tier1"]
    none = [r for r in measured if r["kind"] == "none"]
    stopped_err = [r for r in measured if r["kind"] == "stopped"]

    b1_rows = [r for r in measured if r["k"] in (0, 1)]
    b1_diff = [r["name"] for r in b1_rows
               if r["current"] != r["candidate"] or r["audit_changed"]]
    b2_new = [r["name"] for r in none if r["candidate"] != r["current"]]
    b3_lost = [r["name"] for r in measured
               if (r["current"] == "unsat" and r["candidate"] != "unsat")
               or (r["current"] == "absorbed" and r["candidate"] != "absorbed")]
    beyond_sat = [r for r in tier1 if r["band"] == "beyond_group_tolerance"
                  and r["current"] != "unsat"]
    b4_left = [r["name"] for r in beyond_sat if r["candidate"] == "sat_not_absorbed"]

    def by_band(key):
        out = {}
        for band in ("within_group_tolerance", "beyond_group_tolerance"):
            rs = [r for r in tier1 if r["band"] == band]
            out[band] = {"groups": len(rs), "slip": sum(r[key] for r in rs)}
        return out

    def by_k_multi(key):
        return {str(k): sum(r[key] for r in tier1 if r["k"] == k) for k in STOP_COUNTS}

    changed = {kind: {str(k): sum(r["audit_changed"] for r in measured
                                  if r["kind"] == kind and r["k"] == k)
                      for k in STOP_COUNTS}
               for kind in ("none", "tier1", "stopped")}

    return {
        "groups_total": len(rows),
        "excluded": len(rows) - len(measured),
        "excluded_names": [r["name"] for r in rows if "excluded" in r],
        "measured": len(measured),
        "criteria": {
            "B1_k0_k1_identical": {"pass": not b1_diff, "groups": len(b1_rows),
                                   "differing": b1_diff},
            "B2_negative_control_new_absorbed": {"pass": not b2_new, "groups": len(none),
                                                 "new_absorbed": b2_new},
            "B3_no_loss": {"pass": not b3_lost, "lost": b3_lost},
            "B4_beyond_group_tolerance_slip": {
                "pass": not b4_left, "sat_groups": len(beyond_sat), "left": b4_left},
        },
        "all_criteria_pass": not (b1_diff or b2_new or b3_lost or b4_left),
        "slip_by_band": {"current": by_band("current_slip"),
                         "candidate": by_band("candidate_slip")},
        "slip_by_k": {"current": by_k_multi("current_slip"),
                      "candidate": by_k_multi("candidate_slip")},
        "tier1_slip_table": _slip_table(measured),
        "audit_changed_by_kind_and_k": changed,
        "audit_changed_total": sum(r["audit_changed"] for r in measured),
        "within_element_tolerance_tier1": {
            "groups": sum(r["band"] == "within_element_tolerance" for r in tier1),
            "current": dict(Counter(r["current"] for r in tier1
                                    if r["band"] == "within_element_tolerance")),
            "candidate": dict(Counter(r["candidate"] for r in tier1
                                      if r["band"] == "within_element_tolerance")),
        },
        "stopped_error": {
            str(k): {
                "groups": sum(r["k"] == k for r in stopped_err),
                "current": dict(Counter(r["current"] for r in stopped_err if r["k"] == k)),
                "candidate": dict(Counter(r["candidate"] for r in stopped_err
                                          if r["k"] == k)),
                "correct_tier1_newly_audited_groups": sum(
                    r["audit_changed"] for r in stopped_err if r["k"] == k),
            }
            for k in STOP_COUNTS if k > 0
        },
        "negative_control": {
            "groups": len(none),
            "current": dict(Counter(r["current"] for r in none)),
            "candidate": dict(Counter(r["candidate"] for r in none)),
        },
    }


def main() -> None:
    out = (Path(sys.argv[1]) if len(sys.argv) > 1
           else ROOT / "docs/group_total_multi_stop_result.json")
    rows = grid_rows()
    result = {
        "criteria": "docs/group_total_multi_stop_criteria.md",
        "synthetic_only": True,
        "judgment_code_changed": False,
        "summary": summarize(rows),
        "rows": rows,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
