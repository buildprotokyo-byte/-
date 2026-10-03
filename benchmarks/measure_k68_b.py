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


def _k65() -> dict[str, Any]:
    return json.loads(K65_RESULT.read_text(encoding="utf-8"))


def checklists_of(drafts: Mapping[str, Mapping[str, Any]], pdf: Path | None) -> dict[str, Any]:
    """K-67 の工事チェック表(K-65 の測り方と同じ。文字の層だけ読む。AI は呼ばない)。"""
    if pdf is None:
        return {}
    from draft import work_checklist

    out = {}
    for name, draft in drafts.items():
        pages = sorted({int(n) for n in (draft["読む"].get("読み") or {})})
        out[name] = work_checklist.build(draft["理解"]["項目"], pdf=pdf, pages=pages)
    return out


def build_all(drafts: Mapping[str, Mapping[str, Any]], pdf: Path | None, *,
              how: str = "連鎖の金額順", **extra: Any) -> dict[str, dict[str, Any]]:
    """5 版それぞれのカード(K-65 と同じ形: ほかの 4 版を「ほかの回」にする)。"""
    from benchmarks.measure_question_curve import candidates_of
    from draft import questioning

    checklists = checklists_of(drafts, pdf)
    items_by_run = {name: d["理解"]["項目"] for name, d in drafts.items()}
    out = {}
    for name, draft in drafts.items():
        other = [v for k, v in items_by_run.items() if k != name]
        raw = candidates_of(draft, None)
        out[name] = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"], other_runs=other, how=how,
                                      checklist=checklists.get(name), **extra)
        out[name]["_候補"] = raw
        out[name]["_ほかの回"] = other
    return out


def _auto_confirmed(draft: Mapping[str, Any], cards: Sequence[Mapping[str, Any]],
                    answers: Mapping[str, str]) -> dict[str, Any]:
    """答えを戻した下書きを組み立て、本番の入口(機械の検算)に通して自動確定を数える。"""
    from copy import deepcopy

    from draft import answers_io, stages
    from draft.run import machine_check

    understanding = deepcopy(draft["理解"])
    finish = deepcopy(draft["仕上表"])
    applied = answers_io.apply(understanding, finish, cards, answers)
    rows, _ = stages.assembly_rows(understanding["項目"])
    check = machine_check(rows, None, None, "P011")
    return {"自動確定": check["自動確定"], "戻せなかった答え": len(applied["戻せなかった答え"]),
            "外した項目": len(applied["固定の選択肢で外した項目"]) + len(applied["K-64 の口"]["内訳から外した項目"])}


def _first_real(card: Mapping[str, Any]) -> str | None:
    from benchmarks.measure_question_curve import _first_real_option

    return _first_real_option(card)


def made_cards(draft: Mapping[str, Any], built: Mapping[str, Any], pdf_checklist: Mapping[str, Any] | None
               ) -> list[dict[str, Any]]:
    """作ったカード全部(聞く分+「聞かない(影響小)」で黙らせた分)。捨てたカードは入れない。"""
    from draft import cards as cards_mod
    from draft import chain as chain_mod
    from draft.questioning import other_values

    items = draft["理解"]["項目"]
    classified = uncertainty.classify(items, finish=draft["仕上表"], other_runs=built["_ほかの回"])
    graph = chain_mod.build(items, finish=draft["仕上表"])
    bc = cards_mod.build_cards(built["_候補"], items, graph, classified,
                               other_values=other_values(items, built["_ほかの回"]),
                               spot_check_ids=uncertainty.spot_check_ids(classified))
    frames = [c for c in cards_mod.frame_cards(pdf_checklist) if not cards_mod.invalid_reasons(c)]
    return list(bc["カード"]) + frames


def _count(values: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def round2(drafts: Mapping[str, Mapping[str, Any]], *, pdf: Path | None = None, **_: Any) -> dict[str, Any]:
    """周 2: 状態・有無・まとめ方・科目を固定の選択肢+「どれでもない」にする。"""
    from copy import deepcopy

    from benchmarks.measure_question_curve import NONE_WORDS
    from draft import answers_io, cards as cards_mod

    k65 = _k65()["回"]
    checklists = checklists_of(drafts, pdf)
    builts = build_all(drafts, pdf)
    out: dict[str, Any] = {"回": {}}
    for name, built in builts.items():
        cards = built["カード"]
        made = made_cards(drafts[name], built, checklists.get(name))
        small = set(built["聞かない(影響小)"])
        fixed = [c for c in made if c["型"] in cards_mod.FIXED_OPTIONS and not cards_mod._is_unreadable(c)]
        not_fixed = [c for c in fixed if c["選択肢"] != cards_mod.fixed_options(c["型"])]
        bad_input = [c for c in made if c.get("数字の入力") or c.get("自由記述") or c.get("推奨")]
        # 固定の選択肢の 1 つ 1 つを答えて、黙って捨てられる答えが無いか(実案件の 5 版で。黙らせた分も含む)。
        refused = 0
        tried = 0
        for c in fixed:
            for choice in c["選択肢"]:
                u, f = deepcopy(drafts[name]["理解"]), deepcopy(drafts[name]["仕上表"])
                res = answers_io.apply(u, f, [c], {c["鍵"]: choice})
                tried += 1
                refused += len(res["戻せなかった答え"])
        first = {c["鍵"]: a for c in cards if (a := _first_real(c)) is not None}
        reals = {c["鍵"]: [o for o in c["選択肢"] if not any(w in o for w in NONE_WORDS)] for c in cards}
        second = {k: v[1] for k, v in reals.items() if len(v) > 1}
        out["回"][name] = {
            "カードの数(聞く分)": {"K-65": k65[name]["カードの数"], "周2": len(cards)},
            "型ごと(聞く分)": {"K-65": k65[name]["型ごと"], "周2": built["型ごと"]},
            "作ったカードの数(聞く分+影響小で黙らせた分)": len(made),
            "作ったカードの型ごと": _count([c["型"] for c in made]),
            "影響小で黙らせたカードの型ごと": _count([c["型"] for c in made if c["鍵"] in small]),
            "固定の型のカード(作った分)": len(fixed),
            "線1: 固定の表と違うカード": len(not_fixed),
            "線2: 数字・自由記述・推奨のあるカード": len(bad_input),
            "線3: 固定の選択肢を 1 つずつ答えた数": tried,
            "線3: 戻せなかった答え": refused,
            "捨てたカードの数": {"K-65": k65[name]["捨てたカードの数"], "周2": len(built["捨てたカード"])},
            "捨てたカードの型と理由ごと": _count([f"{d.get('型')}|{d['理由']}" for d in built["捨てたカード"]]),
            "聞かない(影響小)の数": {"K-65": k65[name]["聞かない(影響小)の数"], "周2": len(small)},
            "線4: 聞くカードに 1 つ目の選択肢で答えて組み立てた自動確定": _auto_confirmed(drafts[name], cards, first),
            "線4: 聞くカードに 2 つ目の選択肢で答えて組み立てた自動確定": _auto_confirmed(drafts[name], cards, second),
        }
    out["線1〜4(5版すべて)"] = {
        "線1": all(r["線1: 固定の表と違うカード"] == 0 for r in out["回"].values()),
        "線2": all(r["線2: 数字・自由記述・推奨のあるカード"] == 0 for r in out["回"].values()),
        "線3": all(r["線3: 戻せなかった答え"] == 0 for r in out["回"].values()),
        "線4": all(r[k]["自動確定"] == 0 for r in out["回"].values() for k in r if k.startswith("線4")),
    }
    return out


ROUNDS: dict[int, Callable[..., dict[str, Any]]] = {1: round1, 2: round2}


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
