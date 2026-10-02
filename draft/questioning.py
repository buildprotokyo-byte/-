"""質問の段をひとまとめにする(K-65)。**問いを新しく作らない。**

`draft.stages.question_candidates` が出した問いに、
K-65 の 1(3 状態)・2(連鎖)・3(カードとメーター)・4(選び方)を付ける層。
run.py からはこの 1 本だけを呼ぶ。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from draft import cards as cards_mod
from draft import chain as chain_mod
from draft import uncertainty as unc
from draft.cost_table import match as cost_match
from draft.stages import nfkc


def amounts_of(items: Sequence[Mapping[str, Any]],
               cost_table: Mapping[str, Any] | None) -> tuple[dict[str, float], float | None]:
    """項目ごとの金額と総額。**原価表が無ければ ({}, None)**(0 にしない)。"""
    if not cost_table:
        return {}, None
    out: dict[str, float] = {}
    for it in items:
        row = cost_match(cost_table, it.get("品番"), it.get("工事"), it.get("単位"))
        price = (row or {}).get("単価")
        qty = it.get("数量")
        if price is None or qty is None:
            continue
        out[it["id"]] = float(price) * float(qty)
    total = sum(float(r["単価"]) * float(r["数量"]) for r in cost_table["行"]
                if r.get("単価") is not None and r.get("数量") is not None)
    return out, total or None


def cost_rows_by_key(cost_table: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in (cost_table or {}).get("行") or ():
        for field in ("品番", "工事"):
            key = nfkc(row.get(field))
            if key:
                out.setdefault(key, row)
    return out


def build(questions: Mapping[str, Any], understanding: Mapping[str, Any],
          finish: Mapping[str, Any] | None, *, cost_table: Mapping[str, Any] | None = None,
          other_runs: Sequence[Sequence[Mapping[str, Any]]] = (),
          crops: Mapping[str, Any] | None = None, how: str = "連鎖の金額順",
          seed: int = 65) -> dict[str, Any]:
    items = list(understanding.get("項目") or ())
    classified = unc.classify(items, finish=finish, other_runs=other_runs,
                              cost_rows=cost_rows_by_key(cost_table))
    graph = chain_mod.build(items, finish=finish)
    amounts, total = amounts_of(items, cost_table)

    raw = list(questions.get("候補") or ())
    if not raw:
        # `stages.questions` は段階ごとに切った分だけを返すので、全部を集めて重複を外す。
        seen: set[str] = set()
        for mode_cards in (questions.get("段階ごと") or {}).values():
            for q in mode_cards:
                if q["鍵"] not in seen:
                    seen.add(q["鍵"])
                    raw.append(q)

    built = cards_mod.build_cards(raw, items, graph, classified, amounts=amounts, total=total, crops=crops)
    has_cost = bool(total)
    ordered = cards_mod.order(built["カード"], how=how, has_cost=has_cost, seed=seed)

    spots = set(unc.spot_check_ids(classified))
    for c in ordered:
        if spots & set(c.get("直接") or ()):
            c["抜き取り"] = True

    small = [c["鍵"] for c in ordered
             if unc.small_effect((c.get("メーター", {}).get("確定する金額") or {}).get("直接")
                                 if isinstance(c.get("メーター", {}).get("確定する金額"), Mapping) else None,
                                 total, c.get("メーター", {}).get("確定する項目数", {}).get("合計", 0))]
    asked = [c for c in ordered if c["鍵"] not in set(small) or c.get("抜き取り")]
    rows = cards_mod.cumulative(asked, total=total, item_total=len(items) or None)

    return {
        "3状態": {k: v for k, v in classified.items() if k != "項目ごと"},
        "項目の3状態": classified["項目ごと"],
        "連鎖のグラフ": graph.as_dict(),
        "並べ方": how + ("(金額)" if has_cost else "(項目数。原価表が無いので金額ではない)"),
        "カード": asked,
        "型ごと": {t: sum(1 for c in asked if c["型"] == t) for t in cards_mod.TYPES},
        "捨てたカード": built["捨てたカード"],
        "畳んだカード": built["畳んだカード"],
        "抜き取りの問い": [c["鍵"] for c in asked if c.get("抜き取り")],
        "聞かない(影響小)": small,
        "累積": rows,
        "止め線に入る問数": {f"{s // 60}分": cards_mod.within(rows, s) for s in cards_mod.STOP_LINES},
        "但し書き": built["但し書き"] + ["質問数の上限は置いていない(時間の止め線だけ)"],
    }
