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


def other_values(items: Sequence[Mapping[str, Any]],
                 other_runs: Sequence[Sequence[Mapping[str, Any]]]) -> dict[tuple[str, str, str, str], list[str]]:
    """ほかの回の読みが出した数量(`数量` の型の選択肢に使う)。

    **これは 3 回読んだときだけ在る。**1 回しか読んでいない本番では空になり、
    `数量` のカードは選択肢が足りずに捨てられる(K-65 で実測)。
    """
    want = {unc.item_key(it) for it in items}
    out: dict[tuple[str, str, str, str], list[str]] = {}
    for run in other_runs:
        for it in run:
            key = unc.item_key(it)
            if key not in want or it.get("数量") is None:
                continue
            text = f"{it['数量']:g}{nfkc(it.get('単位'))}"
            if text not in out.setdefault(key, []):
                out[key].append(text)
    return out


#: 数量の選択肢の出どころ(K-68 B 周 3)。この順に集め、選択肢は値の小さい順に並べる(どれが推しかは書かない)。
QUANTITY_SOURCES = ("同じものの値", "3回の値", "3回の値(K-66 の鍵)", "機械の値", "縮尺で換算した値")

_UNIT_ALIASES = {"㎡": "m2", "m²": "m2", "ｍ２": "m2", "m^2": "m2"}


def unit_key(unit: Any) -> str:
    text = nfkc(unit).replace(" ", "")
    return _UNIT_ALIASES.get(text, text)


def _value(qty: Any) -> float | None:
    """数量として使える値。**無い(未取得)・0 以下・数でないものは使わない**(0 を作らない)。"""
    if isinstance(qty, bool) or not isinstance(qty, (int, float)):
        return None
    return float(qty) if qty > 0 else None


def quantity_sources(items: Sequence[Mapping[str, Any]], other_runs: Sequence[Sequence[Mapping[str, Any]]] = (), *,
                     machine: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
                     scale: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """数量が無い項目ごとの、数量の候補の値(K-68 B 周 3)。**AI は呼ばない。値を作らない。**

    1 つの値は ``{"値", "単位", "出どころ", "辿る"}``。``辿る`` はその値を読んだ回・項目・行・ページ。
    ``machine`` と ``scale`` は項目 id ごとの ``{"値", "単位", "辿る"}`` の並び
    (`machine_values_from` と `scale_values_from` で作る)。
    単位が項目と違う値は使わない(項目に単位が無ければ値の単位のまま)。
    """
    by_id = {it["id"]: it for it in items}
    old_index: list[dict[tuple[str, str, str, str], list[Mapping[str, Any]]]] = []
    new_index: list[dict[tuple[Any, ...], list[Mapping[str, Any]]]] = []
    for run in other_runs:
        o: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
        n: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for it in run:
            o.setdefault(unc.item_key(it), []).append(it)
            n.setdefault(unc.agreement_item_key(it), []).append(it)
        old_index.append(o)
        new_index.append(n)
    own_new: dict[tuple[Any, ...], int] = {}
    for it in items:
        k = unc.agreement_item_key(it)
        own_new[k] = own_new.get(k, 0) + 1

    out: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it.get("数量") is not None:
            continue
        unit = unit_key(it.get("単位"))
        found: list[dict[str, Any]] = []

        def add(qty: Any, u: Any, source: str, trace: Mapping[str, Any]) -> None:
            v = _value(qty)
            if v is None or (unit and unit_key(u) and unit_key(u) != unit):
                return
            found.append({"値": v, "単位": nfkc(u) or nfkc(it.get("単位")), "出どころ": source, "辿る": dict(trace)})

        for i in it.get("同じもの") or ():
            if i in by_id:
                add(by_id[i].get("数量"), by_id[i].get("単位"), "同じものの値", {"回": "この回", "項目": i})
        for r, index in enumerate(old_index):
            for other in index.get(unc.item_key(it), ()):
                add(other.get("数量"), other.get("単位"), "3回の値", {"回": f"ほかの回{r + 1}", "項目": other.get("id")})
        key = unc.agreement_item_key(it)
        if own_new.get(key) == 1:
            for r, index in enumerate(new_index):
                rows = index.get(key, ())
                if len(rows) == 1:
                    add(rows[0].get("数量"), rows[0].get("単位"), "3回の値(K-66 の鍵)",
                        {"回": f"ほかの回{r + 1}", "項目": rows[0].get("id")})
        for source, table in (("機械の値", machine), ("縮尺で換算した値", scale)):
            for m in (table or {}).get(it["id"], ()):
                add(m.get("値"), m.get("単位"), source, m.get("辿る") or {})
        if found:
            out[it["id"]] = found
    return out


def machine_values_from(production_rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """K-61 が保存した本番の形(`本番の形.json` の `工事項目`)から、機械の検算が並べた値を項目ごとに取る。

    **機械を動かし直さない。**AI が数量を出さず、機械の数を並べた行(`機械の検算` の欄がある行)だけ。
    1 行に機械の値が 2 つ以上あれば、どれも候補にする(足さない)。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in production_rows:
        if row.get("数量") is not None:
            continue
        machine = row.get("機械の検算") or (row.get("extra") or {}).get("機械の検算") or ()
        ids = [i.strip() for b in row.get("根拠") or () if isinstance(b, Mapping)
               for i in str(b.get("根拠") or "").split(",") if i.strip()]
        for m in machine:
            for i in ids:
                out.setdefault(i, []).append({"値": m.get("数量"), "単位": m.get("単位"),
                                              "辿る": {"機械の行": m.get("機械の番号"), "道": m.get("道"),
                                                     "本番の形の行": row.get("番号")}})
    return out


def scale_values_from(part: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """`draft.flags.scale_length` の出力から、縮尺で測った値を理解の項目ごとに取る。

    **値が 1 つに決まらなかった室(`数量` が無い)は使わない。**
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for a in (part or {}).get("足したもの") or ():
        if a.get("数量") is None:
            continue
        basis = a.get("根拠") or {}
        for i in a.get("理解の項目") or ():
            out.setdefault(i, []).append({"値": a["数量"], "単位": a.get("単位"),
                                          "辿る": {"ページ": list(basis.get("ページ") or ()),
                                                 "測った量": basis.get("測った量")}})
    return out


def build(questions: Mapping[str, Any], understanding: Mapping[str, Any],
          finish: Mapping[str, Any] | None, *, cost_table: Mapping[str, Any] | None = None,
          other_runs: Sequence[Sequence[Mapping[str, Any]]] = (),
          crops: Mapping[str, Any] | None = None, how: str = "連鎖の金額順",
          checklist: Mapping[str, Any] | None = None, seed: int = 65,
          machine_values: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
          scale_values: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
          quantity_rule: str = "新") -> dict[str, Any]:
    """問いの候補にカードとメーターを付ける。

    ``quantity_rule`` は `数量` の型の選択肢の作り方。`新`(K-68 B 周 3)は `quantity_sources` の 5 つの出どころ、
    `旧`(K-65)は同じものの値とほかの回の値(旧の鍵)だけ。比べるためだけに旧を残す。
    """
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

    spots = unc.spot_check_ids(classified)
    qsrc = (quantity_sources(items, other_runs, machine=machine_values, scale=scale_values)
            if quantity_rule == "新" else None)
    built = cards_mod.build_cards(raw, items, graph, classified, amounts=amounts, total=total,
                                  crops=crops, other_values=other_values(items, other_runs),
                                  spot_check_ids=spots, quantity_sources=qsrc)
    has_cost = bool(total)
    # K-65 の 0: 工事チェック表の「分からないこと」も問いにする。**原本に依らない問い。**
    # K-68 B 周 6: 理由ごとに問いの形を変える(`cards.reason_cards`)。
    reasons = cards_mod.reason_cards(checklist, items, quantity_sources=qsrc or {}, crops=crops)
    frames = reasons["カード"]
    for c in frames:
        if c["型"] == "数量":
            c["メーター"] = cards_mod._meter(c, graph, amounts, total)  # 連鎖も付く
            continue
        c["メーター"] = {"決める項目数": {"直接": 0, "連鎖": 0, "合計": 0},
                     "決める金額": "未取得(枠の有無を決める問いなので項目の金額では測れない)",
                     "回答時間の見積(秒)": cards_mod.SECONDS[c["型"]],
                     "回答時間の見積は較正済みか": cards_mod.SECONDS_CALIBRATED,
                     "見込み精度の変化": "出さない(未較正)",
                     "但し書き": "枠の分からないことを埋める問い。項目は決めない"}
        c["連鎖"] = {"直接": [], "連鎖": [], "辿った辺の種類": []}
    # 同じ項目の数量の問いは 1 枚にする(理由から作った方を残す。影響小で黙らないため)。
    framed = {i for c in frames if c["型"] == "数量" for i in c["直接"]}
    item_cards = [c for c in built["カード"]
                  if not (c["型"] == "数量" and set(c.get("直接") or ()) & framed)]
    ordered = cards_mod.order(item_cards + frames, how=how, has_cost=has_cost, seed=seed)

    spot_set = set(spots)
    for c in ordered:
        if spot_set & set(c.get("直接") or ()):
            c["抜き取り"] = True

    small = [c["鍵"] for c in ordered
             if not c.get("抜き取り") and not c.get("枠の問い") and unc.small_effect(
                 (c.get("メーター", {}).get("決める金額") or {}).get("直接")
                 if isinstance(c.get("メーター", {}).get("決める金額"), Mapping) else None,
                 total, c.get("メーター", {}).get("決める項目数", {}).get("合計", 0))]
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
        "枠の問い": [c["鍵"] for c in asked if c.get("枠の問い")],
        "枠の理由ごと": reasons["理由ごと"],
        "聞かない(影響小)": small,
        "累積": rows,
        "止め線に入る問数": {f"{s // 60}分": cards_mod.within(rows, s) for s in cards_mod.STOP_LINES},
        "但し書き": built["但し書き"] + ["質問数の上限は置いていない(時間の止め線だけ)"],
    }
