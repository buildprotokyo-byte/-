"""K-70 作業 3 の**パソコン側**: 正解を使って、割れた値のカードを測る。**クラウドでは動かさない(正解を開くため)。**

基準は `docs/k70_split_value_cards_criteria.md`。手順書は共有フォルダの K-70(`手順書_割れた値のカード.md`)。
AI は 1 回も呼ばない。正解はパソコンの中だけで開き、**出すのは件数と割合だけ**(行の名前・室名・数量そのものは出さない)。

測るもの:

1. **選択肢に正しい値が無い割合**(正解と照合できたカードのうち)。
2. 「理想の人」(正解に合う選択肢を選ぶ。無ければ「どれでもない」)の答えを戻したとき、照合できたカードの行(基準の回)で
   正解と合う行が何行から何行になったか。合っていた行が合わなくなった数。
3. 合成の方針 A(1 つ目を選ぶ)の答えが正解だった割合(機械が選んではいけない理由を数で見る)。
4. 人の本物の答え(「1-3、2-1 …」)があれば、その答えが正解だった割合と、戻した後の動き。
5. 自動確定(0 のはず。1 件でも出たら止める)。

照合のしかた(K-66 の鍵。品名の文字は見ない):

- 数量: 正解の行のうち、室を外した鍵がカードの行と同じものが **ちょうど 1 行**で、基準の回の読みにもその鍵の行が
  **1 行だけ**のとき(正解は室ごとでなく合計なので、読みに同じ工事が 2 か所以上あれば比べられない)。
  選択肢の値が正解の数量と K-66 の許容差の中なら「正しい選択肢」。
- 状態・部位: 正解の行のうち、室とその欄を外した鍵が同じものの、その欄の値。選択肢と同じ値があれば「正しい選択肢」。
- 室: 正解に室の列があるとき(`--col 室=列名`)だけ照合する。無ければ「照合できない(正解に室が無い)」。

使い方::

    PYTHONPATH=. python -m benchmarks.measure_k70_split_cards_truth \\
      --pdf <匿名化 v4 の PDF> --runs <full_R1> <full_R2> <full_R3> --golden <正解.json> \\
      [--answer-text "1-3、2-1、…"] [--col 数量=列名] --out <結果.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft import answers_io, split_cards

#: 測る前に決めた線(手順書にも同じことを書く)。
LINE_NO_RIGHT_OPTION_MAX = 0.30
MIN_JUDGED_CARDS = 5


def _qty_of(option: str) -> tuple[float, str] | None:
    from sameness.quantity import canonical_unit

    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", option)
    return (float(m.group(1)), canonical_unit(m.group(2).strip())) if m else None


def _no_room_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    from sameness.rows import agreement_key

    return agreement_key(row, with_room=False)


def _compatible(gold_key: tuple[Any, ...], row_key: tuple[Any, ...], skip: int | None = None) -> bool:
    """正解の鍵と読みの鍵が合うか。**正解で取れなかった欄(None)は比べない**(正解の品名に状態が書いていない等)。

    ``skip`` の位置(状態・部位の照合で見る欄)は比べない。品名の鍵は文字まで同じときだけ。
    """
    if len(gold_key) != len(row_key) or gold_key[0] != row_key[0]:
        return False
    if gold_key[0] != "細目":
        return gold_key == row_key
    return all(g is None or g == r for n, (g, r) in enumerate(zip(gold_key, row_key)) if n != skip)


def _wild(key: tuple[Any, ...], dim: str) -> tuple[Any, ...] | None:
    if key[0] != "細目":
        return None
    k = list(key)
    k[{"部位": 2, "状態": 3}[dim]] = "*"
    return tuple(k)


def _label(raw: Any, dim: str) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    prefix = {"部位": "BU_", "状態": "JO_"}[dim]
    return text[len(prefix):] if text.startswith(prefix) else text


def judge(card: Mapping[str, Any], runs: Mapping[str, Sequence[Mapping[str, Any]]], base: str,
          gold: Any, room_col: str | None = None) -> dict[str, Any]:
    """1 枚のカードを正解と照合する。**返すのは照合できたか・正しい選択肢の番号・理由だけ**(値は返さない)。"""
    from sameness import quantity_verdict

    index = {n: {it["id"]: it for it in rows} for n, rows in runs.items()}
    rows = [index[n][i] for n, i in (card.get("行") or {}).items() if n in index and i in index[n]]
    if not rows:
        return {"照合": False, "理由": "カードの行が無い"}
    options = split_cards.real_options(card)
    dim = card["次元"]
    gold_keys = [_no_room_key(gold.fields(i)) for i in range(len(gold.items))]
    if dim == "数量":
        key = _no_room_key(rows[0])
        hits = [i for i, k in enumerate(gold_keys) if _compatible(k, key)]
        if len(hits) != 1:
            return {"照合": False, "理由": f"正解の行が {'0' if not hits else '2 以上'}"}
        same_in_base = sum(1 for it in runs[base] if any(_compatible(gold_keys[h], _no_room_key(it)) for h in hits))
        if same_in_base != 1:
            return {"照合": False, "理由": "読みに同じ工事が 2 か所以上(正解は合計なので比べられない)"}
        truth = gold.quantity(hits[0])
        if truth is None:
            return {"照合": False, "理由": "正解に数量が無い"}
        from sameness.quantity import canonical_unit

        unit = canonical_unit(gold.unit(hits[0]))
        right = [n for n, o in enumerate(options, 1) if (q := _qty_of(o)) is not None and (not unit or q[1] == unit)
                 and quantity_verdict(truth, q[0], unit).value != "違う"]
        return {"照合": True, "正しい選択肢": right, "理由": "" if right else "正しい値が選択肢に無い"}
    if dim in ("状態", "部位"):
        pos = {"部位": 2, "状態": 3}[dim]
        wild = {_wild(_no_room_key(r), dim) for r in rows}
        if None in wild or len(wild) != 1:
            return {"照合": False, "理由": "鍵が細目の形でない"}
        row_key = _no_room_key(rows[0])
        values = {_label(k[pos], dim) for k in gold_keys if _compatible(k, row_key, skip=pos)}
        values.discard(None)
        if not values:
            return {"照合": False, "理由": "正解の行が 0"}
        right = [n for n, o in enumerate(options, 1) if o in values]
        return {"照合": True, "正しい選択肢": right, "理由": "" if right else "正しい値が選択肢に無い"}
    if dim == "室":
        if not room_col:
            return {"照合": False, "理由": "正解に室が無い"}
        key = _no_room_key(rows[0])
        rooms = {split_cards.spelling(gold.raw[i].get(room_col)) for i, k in enumerate(gold_keys)
                 if _compatible(k, key) and gold.raw[i].get(room_col)}
        if not rooms:
            return {"照合": False, "理由": "正解の行が 0"}
        right = [n for n, o in enumerate(options, 1) if split_cards.spelling(o) in rooms]
        return {"照合": True, "正しい選択肢": right, "理由": "" if right else "正しい値が選択肢に無い"}
    return {"照合": False, "理由": "次元が無い"}


def _matches(card: Mapping[str, Any], runs: Mapping[str, Sequence[Mapping[str, Any]]], base: str,
             verdict: Mapping[str, Any]) -> bool | None:
    """基準の回のカードの行が、いま正解と合っているか(照合できたカードだけ)。"""
    if not verdict.get("照合") or base not in (card.get("行") or {}):
        return None
    it = next((x for x in runs[base] if x["id"] == card["行"][base]), None)
    if it is None:
        return None
    now = split_cards.dim_value(it, card["次元"])
    right = {split_cards.real_options(card)[n - 1] for n in verdict.get("正しい選択肢") or ()}
    if card["次元"] == "室":
        return now is not None and split_cards.spelling(now) in {split_cards.spelling(r) for r in right}
    return now in right


def measure(real: Mapping[str, Any], gold: Any, *, answer_text: str | None = None, count: int = 10,
            room_col: str | None = None) -> dict[str, Any]:
    from draft import stages
    from draft.run import machine_check

    base = real["base"]
    cards = split_cards.numbered(real["ordered"])
    verdicts = {c["鍵"]: judge(c, real["runs"], base, gold, room_col) for c in cards}
    judged = [c for c in cards if verdicts[c["鍵"]]["照合"]]
    missing = [c for c in judged if not verdicts[c["鍵"]]["正しい選択肢"]]
    reasons: dict[str, int] = {}
    for v in verdicts.values():
        if not v["照合"]:
            reasons[v["理由"]] = reasons.get(v["理由"], 0) + 1

    def answers_for(policy: str, subset: Sequence[Mapping[str, Any]]) -> dict[str, str]:
        out = {}
        for c in subset:
            v = verdicts[c["鍵"]]
            if policy == "理想":
                right = v.get("正しい選択肢") or []
                out[c["鍵"]] = (split_cards.real_options(c)[right[0] - 1] if right
                               else split_cards.NONE_OF_THESE if v["照合"] else split_cards.DONT_KNOW)
            else:
                out[c["鍵"]] = split_cards.real_options(c)[0]
        return out

    def run(answers: Mapping[str, str], subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        runs = deepcopy(real["runs"])
        shown = split_cards.numbered(subset)
        parsed = answers_io.parse_numbered(answers_io.numbered_text(shown, answers), shown)
        before = [m for c in shown if (m := _matches(c, runs, base, verdicts[c["鍵"]])) is not None]
        before_by = {c["鍵"]: _matches(c, runs, base, verdicts[c["鍵"]]) for c in shown}
        applied = split_cards.apply(runs, shown, parsed["答え"])
        after_by = {c["鍵"]: _matches(c, runs, base, verdicts[c["鍵"]]) for c in shown}
        rows, _ = stages.assembly_rows(runs[base])
        return {"照合できたカードの行": len(before),
                "正解と合う行 前→後": [sum(1 for v in before_by.values() if v), sum(1 for v in after_by.values() if v)],
                "合っていたのに合わなくなった行": sum(1 for k in before_by if before_by[k] and after_by[k] is False),
                "戻せなかった答え": len(parsed["戻せなかった答え"]) + len(applied["戻せなかった答え"]),
                "自動確定": machine_check(rows, None, None, "P011")["自動確定"]}

    out: dict[str, Any] = {
        "カード": len(cards), "照合できたカード": len(judged),
        "照合できなかった理由": reasons,
        "照合できたカードの次元ごと": _count([c["次元"] for c in judged]),
        "選択肢に正しい値が無いカード": len(missing),
        "選択肢に正しい値が無い割合": round(len(missing) / len(judged), 4) if judged else None,
        "選択肢に正しい値が無いカードの次元ごと": _count([c["次元"] for c in missing]),
        "方針 A(1 つ目)が正しかったカード": sum(1 for c in judged if 1 in (verdicts[c["鍵"]]["正しい選択肢"] or ())),
        "理想の人(全部のカード)": run(answers_for("理想", cards), cards),
        "理想の人(PDF の 10 枚)": run(answers_for("理想", cards[:count]), cards[:count]),
        "方針 A(全部のカード)": run(answers_for("A", cards), cards),
    }
    if answer_text:
        shown = cards[:count]
        parsed = answers_io.parse_numbered(answer_text, shown)
        right = sum(1 for k, choice in parsed["答え"].items() if verdicts[k]["照合"] and choice in
                    {split_cards.real_options(c)[n - 1] for c in shown if c["鍵"] == k
                     for n in verdicts[k]["正しい選択肢"] or ()})
        answered_judged = sum(1 for k in parsed["答え"] if verdicts[k]["照合"])
        out["人の答え(PDF の 10 枚)"] = {
            "読めた答え": len(parsed["答え"]), "戻せなかった答え": len(parsed["戻せなかった答え"]),
            "戻せなかった理由": _count([r["理由"] for r in parsed["戻せなかった答え"]]),
            "どれでもない・分からない": sum(1 for v in parsed["答え"].values() if v in split_cards.TAIL),
            "照合できた答え": answered_judged, "正しかった答え": right,
            "戻した後": run(parsed["答え"], shown)}
    judged_n = len(judged)
    ideal = out["理想の人(全部のカード)"]
    out["線"] = {
        "線a 選択肢に正しい値が無い割合 0.30 以下": (None if judged_n < MIN_JUDGED_CARDS else
                                       out["選択肢に正しい値が無い割合"] <= LINE_NO_RIGHT_OPTION_MAX),
        "線b 理想の答えで正解と合う行が増え、合わなくなった行が 0": (
            None if judged_n < MIN_JUDGED_CARDS else
            ideal["正解と合う行 前→後"][1] > ideal["正解と合う行 前→後"][0] and ideal["合っていたのに合わなくなった行"] == 0),
        "線c 自動確定 0": all(v.get("自動確定", 0) == 0 for v in out.values() if isinstance(v, Mapping)
                          and "自動確定" in v),
        "但し書き": f"照合できたカードが {MIN_JUDGED_CARDS} 枚未満なら線 a・b は「判定できない」(null)",
    }
    return out


def _count(values: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    from benchmarks.make_k70_split_cards import build_real
    from benchmarks.pc_kit import Gold, parse_cols

    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--golden", type=Path, required=True)
    p.add_argument("--col", action="append", default=[], help="符号/品名/単位/科目/中科目/数量/金額=列名")
    p.add_argument("--room-col", default=None, help="正解に室の列があればその名前")
    p.add_argument("--answer-text", default=None, help="人の答え(「1-3、2-1 …」)")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    gold = Gold(a.golden, parse_cols(a.col))
    real = build_real(a.runs, a.pdf)
    result = measure(real, gold, answer_text=a.answer_text, room_col=a.room_col)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not result["線"]["線c 自動確定 0"]:
        print("自動確定が 1 件以上あります。ここで止めて報告してください", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
