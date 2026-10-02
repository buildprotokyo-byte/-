"""K-65 の 6: 質問数と、構造から出る数字の曲線。**正解は開かない。**

使い方(K-61 が保存した出力を読むだけ。AI は 1 回も呼ばない)。

    PYTHONPATH=. python benchmarks/measure_question_curve.py \
      --runs /mnt/project-files/reports/K-61/結果/P011/full_R1 ... \
      --out docs/k65_question_curve_result.json

出すのは件数と割合だけ(実案件のため、図面の中身・室名・行の名前は書かない)。
"""

from __future__ import annotations

import argparse
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft import answers_io, cards as cards_mod, chain as chain_mod, questioning, stages, uncertainty

COUNTS = (0, 3, 5, 10, 20, 40, 60)
ARMS = ("連鎖の金額順", "ランダム", "質問ゼロでAIが埋める")
NONE_WORDS = ("どれでもない", "分からない")


def load_run(path: Path) -> dict[str, Any]:
    return json.loads((path / "下書き.json").read_text(encoding="utf-8"))


def candidates_of(draft: Mapping[str, Any], cost_table: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """保存された出力から問いの候補を組み直す(上限で切る前の全部)。"""
    return stages.question_candidates(draft["理解"], draft["仕上表"], draft["読む"], cost_table)


def _first_real_option(card: Mapping[str, Any]) -> str | None:
    for o in card["選択肢"]:
        if not any(w in o for w in NONE_WORDS):
            return o
    return None


def _apply(draft: Mapping[str, Any], answers: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """答えを戻した後の理解と仕上表(元は書き換えない)。**AI は呼ばない。**"""
    understanding = deepcopy(draft["理解"])
    finish = deepcopy(draft["仕上表"])
    stages.apply_answers(understanding, finish, dict(answers))
    return understanding, finish


def _state_counts(understanding: Mapping[str, Any], finish: Mapping[str, Any],
                  other_runs: Sequence[Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    out = uncertainty.classify(understanding["項目"], finish=finish, other_runs=other_runs)
    return out["3状態の分布"]


def curve(draft: Mapping[str, Any], built: Mapping[str, Any], *, how: str,
          other_runs: Sequence[Sequence[Mapping[str, Any]]], total: float | None) -> dict[str, Any]:
    all_cards = built["カード"]
    rows: list[dict[str, Any]] = []
    base = _state_counts(draft["理解"], draft["仕上表"], other_runs)
    for k in COUNTS:
        chosen = all_cards[:k]
        answers = {}
        for c in chosen:
            pick = _first_real_option(c)
            if pick is not None:
                answers[c["鍵"]] = pick
        understanding, finish = _apply(draft, answers)
        after = _state_counts(understanding, finish, other_runs)
        rows_cum = built["累積"]
        cum = rows_cum[min(k, len(rows_cum)) - 1] if k and rows_cum else None
        rows.append({
            "問数": k,
            "戻せた答え": len(answers),
            "3状態(後)": after,
            "未確定の残り": after[uncertainty.UNSETTLED],
            "未確定が減った数": base[uncertainty.UNSETTLED] - after[uncertainty.UNSETTLED],
            "型ごと": {t: sum(1 for c in chosen if c["型"] == t) for t in cards_mod.TYPES
                    if any(c["型"] == t for c in chosen)},
            "回答時間の見積(秒)": (cum or {}).get("回答時間の見積(秒)", 0),
            "決める項目の割合(構造)": (cum or {}).get("決める項目の割合"),
            "届いた精度": (cum or {}).get("届いた精度", "未取得"),
        })
    return {"並べ方": how, "3状態(前)": base, "行": rows,
            "カードの数": len(all_cards),
            "止め線に入る問数": built["止め線に入る問数"]}


def calibration(draft: Mapping[str, Any], built: Mapping[str, Any],
                other_runs: Sequence[Sequence[Mapping[str, Any]]], *, limit: int = 60) -> dict[str, Any]:
    """メーターの較正。**見込み(確定すると言った項目数)と、実際に未確定から出た数を並べる。**

    正解は使わない。「実際」は 3 状態が未確定から出た項目数(構造の数字)である。
    """
    base = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other_runs)
    base_unsettled = {r["id"] for r in base["項目ごと"] if r["3状態"] == uncertainty.UNSETTLED}
    rows: list[dict[str, Any]] = []
    for c in built["カード"][:limit]:
        pick = _first_real_option(c)
        if pick is None:
            continue
        understanding, finish = _apply(draft, {c["鍵"]: pick})
        after = uncertainty.classify(understanding["項目"], finish=finish, other_runs=other_runs)
        now_unsettled = {r["id"] for r in after["項目ごと"] if r["3状態"] == uncertainty.UNSETTLED}
        actual = len(base_unsettled - now_unsettled)
        promised = c["メーター"]["決める項目数"]["合計"]
        rows.append({"鍵": c["鍵"], "型": c["型"], "見込み": promised, "実際": actual,
                     "ずれ": abs(promised - actual) / promised if promised else None})
    gaps = sorted(r["ずれ"] for r in rows if r["ずれ"] is not None)
    median = gaps[len(gaps) // 2] if gaps else None
    return {
        "測った問数": len(rows),
        "見込みの合計": sum(r["見込み"] for r in rows),
        "実際の合計": sum(r["実際"] for r in rows),
        "ずれの中央値": round(median, 4) if median is not None else None,
        "線: ずれの中央値が 20% を超えたら見込みは画面に出さない":
            "出さない" if (median is not None and median > 0.20) else "出してよい",
        "見込みが実際より大きい問の数": sum(1 for r in rows if r["実際"] < r["見込み"]),
        "実際が 0 だった問の数": sum(1 for r in rows if r["実際"] == 0),
        "型ごとのずれの中央値": _by_type(rows),
    }


def _by_type(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in cards_mod.TYPES:
        gaps = sorted(r["ずれ"] for r in rows if r["型"] == t and r["ずれ"] is not None)
        if gaps:
            out[t] = round(gaps[len(gaps) // 2], 4)
    return out


def wrong_answers(draft: Mapping[str, Any], built: Mapping[str, Any],
                  other_runs: Sequence[Sequence[Mapping[str, Any]]], *, share: float,
                  n: int = 40, seed: int = 65) -> dict[str, Any]:
    """回答の ``share`` を間違えたときの食い違いと、矛盾の検出が見つける割合。

    「間違える」= その問の**別の選択肢**を選ぶ(でたらめな文字を入れない)。
    正解は使わないので、ここで言う劣化は「間違えない版との食い違い」である。
    """
    chosen = built["カード"][:n]
    right: dict[str, str] = {}
    for c in chosen:
        pick = _first_real_option(c)
        if pick is not None:
            right[c["鍵"]] = pick
    rng = random.Random(seed)
    keys = sorted(right)
    wrong_keys = rng.sample(keys, max(1, int(len(keys) * share))) if keys else []
    wrong = dict(right)
    for key in wrong_keys:
        card = next(c for c in chosen if c["鍵"] == key)
        others = [o for o in card["選択肢"] if o != right[key]]
        if others:
            wrong[key] = others[0]

    u_right, f_right = _apply(draft, right)
    u_wrong, f_wrong = _apply(draft, wrong)
    before = answers_io.snapshot(u_right["項目"])
    diff = answers_io.changed(before, u_wrong["項目"])

    answered = [{"鍵": k, "選択肢": v} for k, v in wrong.items()]
    found = answers_io.contradictions(answered, chosen, draft["理解"]["項目"])
    detect = answers_io.wrong_answer_detection(answered, wrong_keys, found)
    return {
        "間違えた割合": share,
        "答えた問数": len(right),
        "間違えた問数": len(wrong_keys),
        "食い違った項目数": len(diff),
        "3状態(正しく答えた版)": _state_counts(u_right, f_right, other_runs),
        "3状態(間違えた版)": _state_counts(u_wrong, f_wrong, other_runs),
        "矛盾の検出": detect,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--cost-table", default=None)
    p.add_argument("--pdf", type=Path, default=None,
                   help="K-67 の工事チェック表を作るための PDF(文字の層だけ読む。AI は呼ばない)")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()

    from draft.cost_table import load_cost_table

    cost_table = load_cost_table(a.cost_table)
    drafts = {path.name: load_run(path) for path in a.runs}
    checklists: dict[str, Any] = {}
    if a.pdf:
        from draft import work_checklist

        for name, draft in drafts.items():
            pages = sorted({int(n) for n in (draft["読む"].get("読み") or {})})
            checklists[name] = work_checklist.build(draft["理解"]["項目"], pdf=a.pdf, pages=pages)
    items_by_run = {name: d["理解"]["項目"] for name, d in drafts.items()}

    result: dict[str, Any] = {
        "但し書き": "件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない",
        "回": {},
    }
    for name, draft in drafts.items():
        other = [v for k, v in items_by_run.items() if k != name]
        raw = candidates_of(draft, cost_table)
        amounts, total = questioning.amounts_of(draft["理解"]["項目"], cost_table)
        per_arm: dict[str, Any] = {}
        builts: dict[str, Any] = {}
        for how in ("連鎖の金額順", "ランダム"):
            built = questioning.build({"候補": raw}, draft["理解"], draft["仕上表"],
                                      cost_table=cost_table, other_runs=other, how=how,
                                      checklist=checklists.get(name))
            builts[how] = built
            per_arm[how] = curve(draft, built, how=how, other_runs=other, total=total)
        base = uncertainty.classify(draft["理解"]["項目"], finish=draft["仕上表"], other_runs=other)
        per_arm["質問ゼロでAIが埋める"] = {
            "並べ方": "質問を 1 つもしない(現行)",
            "3状態": base["3状態の分布"],
            "AI が推論・仮説で埋めている項目数":
                sum(1 for it in draft["理解"]["項目"] if it["状態"] in ("推論", "仮説")),
            "未確定の残り": base["3状態の分布"][uncertainty.UNSETTLED],
        }
        graph = chain_mod.build(draft["理解"]["項目"], finish=draft["仕上表"])
        result["回"][name] = {
            "項目数": len(draft["理解"]["項目"]),
            "問いの候補": len(raw),
            "3状態": base["3状態の分布"],
            "信号ごとの件数": base["信号ごとの件数"],
            "3回の読みで割れた鍵": base["3回の読みで割れた鍵"],
            "連鎖のグラフ": graph.as_dict(),
            "カードの数": len(builts["連鎖の金額順"]["カード"]),
            "捨てたカードの数": len(builts["連鎖の金額順"]["捨てたカード"]),
            "捨てた理由ごと": _reasons(builts["連鎖の金額順"]["捨てたカード"]),
            "捨てたカードの型ごと": _dropped_types(builts["連鎖の金額順"]["捨てたカード"]),
            "候補の種類ごと": {k: sum(1 for q in raw if q["種類"] == k)
                        for k in ("原本との違い", "決められなかった所", "読めなかった所")},
            # **原因の鍵には室名が入るので、件数だけにする**(実案件の名前を書き出さない)。
            "畳んだカード": {"まとめた組の数": len(builts["連鎖の金額順"]["畳んだカード"]),
                        "まとめた数の分布": _fold_sizes(builts["連鎖の金額順"]["畳んだカード"]),
                        "原因の種類ごと": _fold_kinds(builts["連鎖の金額順"]["畳んだカード"])},
            "聞かない(影響小)の数": len(builts["連鎖の金額順"]["聞かない(影響小)"]),
            "数量の型": _quantity_supply(draft, other),
            "読めなかった所の問い": _unreadable(raw, builts["連鎖の金額順"]),
            "抜き取りの問い": len(builts["連鎖の金額順"]["抜き取りの問い"]),
            "枠の問い": len(builts["連鎖の金額順"].get("枠の問い") or ()),
            "型ごと": builts["連鎖の金額順"]["型ごと"],
            "曲線": per_arm,
            "メーターの較正": calibration(draft, builts["連鎖の金額順"], other),
            "間違えた答え": [wrong_answers(draft, builts["連鎖の金額順"], other, share=s)
                       for s in (0.1, 0.2)],
        }
    # K-65 の 8: 資料を隠した版で、質問の数と型が増える向きに動くか。
    full = [n for n in drafts if n.startswith("full")]
    hidden = [n for n in drafts if not n.startswith("full")]
    if full and hidden:
        base_count = min(result["回"][n]["カードの数"] for n in full)
        result["欠けた資料"] = {
            "全部あり版のカードの数(いちばん少ない回)": base_count,
            "版ごと": {n: {"カードの数": result["回"][n]["カードの数"],
                       "問いの候補": result["回"][n]["問いの候補"],
                       "型ごと": result["回"][n]["型ごと"],
                       "足りない資料": drafts[n].get("まとめ", {}).get("足りない資料", "未取得")}
                   for n in hidden},
            "線: 資料を隠した版の質問数が全部あり版を下回らない":
                all(result["回"][n]["カードの数"] >= base_count for n in hidden),
        }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:4000])


def _quantity_supply(draft: Mapping[str, Any], other: Sequence[Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """`数量` の型のカードが作れるか。**3 回読んでも選択肢が揃うか**を数える。"""
    items = draft["理解"]["項目"]
    values = questioning.other_values(items, other)
    by_id = {it["id"]: it for it in items}
    raw = stages.question_candidates(draft["理解"], draft["仕上表"], draft["読む"], None)
    pool = [q for q in raw if q["種類"] == "決められなかった所"
            and by_id.get((q.get("関係する項目") or [""])[0], {}).get("数量") is None]
    supplied = sum(1 for q in pool
                   if values.get(uncertainty.item_key(by_id[q["関係する項目"][0]])))
    return {"数量が無い項目": sum(1 for it in items if it["数量"] is None),
            "数量が無い問いの候補": len(pool),
            "ほかの回が数量を持っていた候補": supplied,
            "1 回の読みだけで作れる数": 0}


def _unreadable(raw: Sequence[Mapping[str, Any]], built: Mapping[str, Any]) -> dict[str, Any]:
    """「読めなかった所」の問いの行き先。**関係する項目が 0 個なので連鎖も 0 になる。**"""
    pool = [q for q in raw if q["種類"] == "読めなかった所"]
    silenced = sum(1 for q in pool if q["鍵"] in set(built["聞かない(影響小)"]))
    asked = sum(1 for c in built["カード"] if c["鍵"].startswith("読めない:"))
    return {"候補": len(pool),
            "関係する項目が 0 個": sum(1 for q in pool if not q.get("関係する項目")),
            "影響小で聞かない": silenced, "カードとして聞く": asked}


def _dropped_types(dropped: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dropped:
        key = str(d.get("型", "型が決まらなかった"))
        out[key] = out.get(key, 0) + 1
    return out


def _fold_sizes(folded: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in folded:
        key = str(f.get("まとめた数"))
        out[key] = out.get(key, 0) + 1
    return out


def _fold_kinds(folded: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """原因の鍵の**前半だけ**(室名や工事の名前が入る後半は捨てる)。"""
    out: dict[str, int] = {}
    for f in folded:
        kind = str(f.get("原因", "")).split(":", 1)[0]
        out[kind] = out.get(kind, 0) + 1
    return out


def _reasons(dropped: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dropped:
        for r in str(d.get("理由", "")).split("・"):
            out[r] = out.get(r, 0) + 1
    return out


if __name__ == "__main__":
    main()
