"""K-68 B: 質問の作り方を直す。周ごとに測る。**正解は開かない。AI は 1 回も呼ばない。**

基準は `docs/k68_b_questions_criteria.md`(周ごとに、測る前にコミットした)。

使い方(K-61 が保存した出力を読むだけ)::

    PYTHONPATH=. python benchmarks/measure_k68_b.py --round 1 \\
      --runs /mnt/project-files/reports/K-61/結果/P011/full_R1 ... \\
      --out docs/k68_b_questions_result.json

出すのは件数と割合だけ(実案件のため、室名・工事の名前・行の名前は書かない)。
`--out` のファイルがあれば、その周の欄だけを書き換える(ほかの周の数字は残す)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from benchmarks.measure_question_curve import load_run
from draft import uncertainty

K65_RESULT = Path(__file__).resolve().parents[1] / "docs" / "k65_question_curve_result.json"


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def round1(drafts: Mapping[str, Mapping[str, Any]], **_: Any) -> dict[str, Any]:
    """周 1: 3 回の割れを K-66 の鍵で揃える。確定・仮説・未確定を旧・新で並べる。"""
    items_by_run = {name: d["理解"]["項目"] for name, d in drafts.items()}
    out: dict[str, Any] = {"回": {}}
    for name, draft in drafts.items():
        other = [v for k, v in items_by_run.items() if k != name]
        row: dict[str, Any] = {"項目数": len(draft["理解"]["項目"])}
        for rule in ("旧", "新"):
            c = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other, rule=rule)
            row[rule] = {
                "3状態": c["3状態の分布"],
                "割れた鍵": c["3回の読みで割れた鍵"],
                "鍵の和": c["鍵の和"],
                "割れた鍵の割合": _ratio(c["3回の読みで割れた鍵"], c["鍵の和"]),
                "全部の回に出た鍵": c["全部の回に出た鍵"],
                "一致の割合(全部の回に出た鍵 ÷ 鍵の和)": _ratio(c["全部の回に出た鍵"], c["鍵の和"]),
                "3回の読みの割れが立った項目": c["信号ごとの件数"]["3回の読みの割れ"],
            }
        flags = {}
        for rule in ("旧", "新"):
            c = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other, rule=rule)
            flags[rule] = {r["id"] for r in c["項目ごと"] if "3回の読みの割れ" in r["信号"]}
        row["割れの立った項目の入れ替わり"] = {
            "旧で割れ・新で割れない": len(flags["旧"] - flags["新"]),
            "旧で割れない・新で割れ": len(flags["新"] - flags["旧"]),
        }
        row["線2: 新の割れた鍵の割合が旧より下がる"] = (
            (row["新"]["割れた鍵の割合"] or 0) < (row["旧"]["割れた鍵の割合"] or 0))
        out["回"][name] = row
    out["線2(5版すべて)"] = all(r["線2: 新の割れた鍵の割合が旧より下がる"] for r in out["回"].values())
    # K-65 と同じ形(5 版をすべて「ほかの回」にする)のほかに、**全部あり版の 3 回だけ**でも並べる。
    full = {k: v for k, v in items_by_run.items() if k.startswith("full")}
    if len(full) >= 2:
        out["全部あり版の3回だけ"] = {}
        for name, items in full.items():
            other = [v for k, v in full.items() if k != name]
            row = {}
            for rule in ("旧", "新"):
                c = uncertainty.classify(items, finish=drafts[name]["仕上表"], other_runs=other, rule=rule)
                row[rule] = {"3状態": c["3状態の分布"], "割れた鍵": c["3回の読みで割れた鍵"],
                             "鍵の和": c["鍵の和"],
                             "割れた鍵の割合": _ratio(c["3回の読みで割れた鍵"], c["鍵の和"]),
                             "全部の回に出た鍵": c["全部の回に出た鍵"],
                             "一致の割合(全部の回に出た鍵 ÷ 鍵の和)": _ratio(c["全部の回に出た鍵"], c["鍵の和"])}
            out["全部あり版の3回だけ"][name] = row
    out["合成の囮と言い換え"] = decoy_check()
    return out


def decoy_check() -> dict[str, Any]:
    """K-66 の囮・言い換えの対を 2 回の読みとして並べ、割れの信号が立つかを数える(合成)。"""
    from sameness.decoys import decoy_pairs, paraphrase_pairs

    def item(name: str, qty: float | None, unit: str) -> dict[str, Any]:
        return {"id": "x", "工事": name, "場所": "室A", "部位": "", "品番": "", "数量": qty, "単位": unit}

    def split(pair: Any) -> bool:
        unit = pair.unit or "m2"
        left = item(pair.left, pair.left_quantity if pair.left_quantity is not None else 10.0, unit)
        right = item(pair.right, pair.right_quantity if pair.right_quantity is not None else 10.0, unit)
        return bool(uncertainty.readings_disagree([[left], [right]], rule="新"))

    decoys = [p for p in decoy_pairs() if p.level == "細目"]
    missed = [p for p in decoys if not split(p)]
    paraphrases = [p for p in paraphrase_pairs() if p.level == "細目"]
    flagged = [p for p in paraphrases if split(p)]
    by_kind: dict[str, int] = {}
    for p in missed:
        by_kind[p.種類] = by_kind.get(p.種類, 0) + 1
    return {
        "囮の対(細目と数量違い)": len(decoys),
        "割れと言えなかった囮": len(missed),
        "割れと言えなかった囮の種類ごと": by_kind,
        "線3: 割れと言えなかった囮が 0 件": not missed,
        "言い換えの対(細目)": len(paraphrases),
        "割れと言った言い換え": len(flagged),
    }


ROUNDS: dict[int, Callable[..., dict[str, Any]]] = {1: round1}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    drafts = {path.name: load_run(path) for path in a.runs}
    result = ROUNDS[a.round](drafts, pdf=a.pdf)
    whole: dict[str, Any] = {}
    if a.out and a.out.exists():
        whole = json.loads(a.out.read_text(encoding="utf-8"))
    whole["但し書き"] = "件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない"
    whole[f"周{a.round}"] = result
    text = json.dumps(whole, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=sorted)[:6000])


if __name__ == "__main__":
    main()
