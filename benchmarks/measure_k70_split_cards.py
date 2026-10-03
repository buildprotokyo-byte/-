"""K-70 作業 3: 割れた値のカードを測る。**正解は開かない。AI は 1 回も呼ばない。**

基準は `docs/k70_split_value_cards_criteria.md`(測る前にコミットした)。

使い方(K-61 が保存した全部あり版の 3 回を読むだけ)::

    PYTHONPATH=. python -m benchmarks.measure_k70_split_cards \\
      --pdf <匿名化 v4 の PDF> \\
      --runs <K-61 の結果>/P011/full_R1 <...>/full_R2 <...>/full_R3 \\
      --out docs/k70_split_value_cards_result.json

出すのは件数と割合だけ(室名・工事名・行の名前は書かない。カードの鍵は名前を含まない記号)。
**答えの方針 A〜D は合成の答えで、正しさの測定ではない。**動かし方だけを数える。
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft import answers_io, split_cards
from draft import uncertainty as unc

POLICIES = ("A 1つ目", "B 多数", "C 分からない", "D どれでもない")
#: 線 9: 方針 A で全部のカードに答えたとき、基準の回の未確定の減り(K-68 B 周 7 の 2〜3 を超える)。
LINE9_MIN_DROP = 4
#: 線 2: 値の割れの鍵のうち、カードに入った鍵の割合。
LINE2_MIN_SHARE = 0.50


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def picks(cards: Sequence[Mapping[str, Any]], policy: str) -> dict[str, str]:
    """合成の答え(基準 2 節で測る前に決めた選び方)。**正しいとは言っていない。**"""
    out: dict[str, str] = {}
    for c in cards:
        real = split_cards.real_options(c)
        if policy.startswith("A"):
            out[c["鍵"]] = real[0]
        elif policy.startswith("B"):
            # 3 回のうち 2 回以上が読んだ値(3 回の値の出どころが 2 つ以上)。無ければ「分からない」。
            many = [o for o in real if sum(1 for s in c["値の出どころ"][o] if s["出どころ"] == "3回の値") >= 2]
            out[c["鍵"]] = many[0] if many else split_cards.DONT_KNOW
        elif policy.startswith("C"):
            out[c["鍵"]] = split_cards.DONT_KNOW
        else:
            out[c["鍵"]] = split_cards.NONE_OF_THESE
    return out


def _snapshot(items: Sequence[Mapping[str, Any]]) -> dict[str, tuple[Any, ...]]:
    return {it["id"]: (it.get("数量"), it.get("単位"), it.get("確度"), it.get("場所"), it.get("区分"),
                       it.get("部位"), it.get("工事")) for it in items}


def _count(values: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def movement(real: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], policy: str, *,
             machine_check: bool = True) -> dict[str, Any]:
    """答えを「1-3、2-1 …」の文字にし、答えの口で読み、3 回の行に戻したときの動き(前 → 後)。"""
    from draft import stages

    base = real["base"]
    finish = real["drafts"][base]["仕上表"]
    runs = deepcopy(real["runs"])
    shown = split_cards.numbered(cards)
    chosen = picks(shown, policy)
    text = answers_io.numbered_text(shown, chosen)
    parsed = answers_io.parse_numbered(text, shown)
    before = split_cards.state_of(runs, base, finish)
    snap = _snapshot(runs[base])
    applied = split_cards.apply(runs, shown, parsed["答え"])
    after = split_cards.state_of(runs, base, finish)
    items = runs[base]
    changed = [i for i, v in _snapshot(items).items() if snap.get(i) != v]
    qty_changed = [it["id"] for it in items if snap[it["id"]][:2] != (it.get("数量"), it.get("単位"))]
    out = {
        "方針": policy, "カード": len(shown), "答えの文字の数": len(text),
        "答えの口で読めた答え": len(parsed["答え"]),
        "答えの口で戻せなかった答え": len(parsed["戻せなかった答え"]),
        "下書きに戻せなかった答え": len(applied["戻せなかった答え"]),
        "書くだけの答え": len(applied["書くだけの答え"]),
        "決めた行(3 回分)": len(applied["決めた行"]),
        "数量が無い項目 前→後": [sum(1 for v in snap.values() if v[0] is None),
                         sum(1 for it in items if it.get("数量") is None)],
        "数量・単位の変わった項目": len(qty_changed),
        "どれかの欄が変わった項目": len(changed),
        "確度 前→後": [_count([v[2] for v in snap.values()]), _count([it.get("確度") for it in items])],
        "3状態 前→後": [before["3状態の分布"], after["3状態の分布"]],
        "未確定の減り": before["3状態の分布"][unc.UNSETTLED] - after["3状態の分布"][unc.UNSETTLED],
        "割れた鍵 / 鍵の和 前→後": [[before["3回の読みで割れた鍵"], before["鍵の和"]],
                              [after["3回の読みで割れた鍵"], after["鍵の和"]]],
        "3回の読みの割れが立った項目 前→後": [before["信号ごとの件数"]["3回の読みの割れ"],
                                   after["信号ごとの件数"]["3回の読みの割れ"]],
    }
    if machine_check:
        from draft.run import machine_check as check

        rows, _ = stages.assembly_rows(items)
        out["自動確定"] = check(rows, None, None, "P011")["自動確定"]
    return out


def _canonical(dim: str, option: str) -> tuple[Any, ...] | str:
    """選択肢の値を K-66 の辞書で揃えた形(数量は値と単位の書き方を揃える)。"""
    import re

    from sameness.quantity import canonical_unit

    if dim == "室":
        return split_cards.spelling(option)
    if dim != "数量":
        return option
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", option)
    if not m:
        return option
    return (float(m.group(1)), canonical_unit(m.group(2).strip()))


def card_shape(cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """基準の線 3・4(カードの形と、名前だけの違い)。"""
    bad_input = traced_missing = made_up = tail_bad = dup = few = meter = name_only = partial = 0
    for c in cards:
        if c.get("推奨") or c.get("数字の入力") or c.get("自由記述"):
            bad_input += 1
        if c.get("見込み"):
            meter += 1
        opts = list(c["選択肢"])
        if opts[-2:] != list(split_cards.TAIL):
            tail_bad += 1
        if len(set(opts)) != len(opts):
            dup += 1
        real = split_cards.real_options(c)
        if len(real) < 2:
            few += 1
        if len({_canonical(c["次元"], o) for o in real}) < len(real):
            partial += 1
        if len({_canonical(c["次元"], o) for o in real}) < 2:
            # 名前だけの違い: K-66 の辞書(単位の書き方)で揃えると、実の値が 1 つになる。
            name_only += 1
        for o in real:
            src = c["値の出どころ"].get(o) or []
            if not src or not all(s.get("辿る", {}).get("回") and s.get("辿る", {}).get("項目") for s in src):
                traced_missing += 1
            if c["次元"] == "数量":
                import re

                m = re.match(r"^([0-9.eE+-]+)", o)
                if not m or float(m.group(1)) <= 0:
                    made_up += 1
            elif not o or o in ("None", "未確定", "未取得"):
                made_up += 1
    return {"推奨・数字の入力・自由記述のあるカード": bad_input, "切り抜きの無いカード": sum(1 for c in cards if not c.get("切り抜き")),
            "実の値が 2 つ未満のカード": few, "出どころを辿れない値": traced_missing, "未取得から作った値": made_up,
            "最後の 2 つがどれでもない・分からないでないカード": tail_bad, "同じ文字の選択肢があるカード": dup,
            "見込みを出すカード": meter, "線4: 実の値が全部同じカード(名前だけの違い)": name_only,
            "書き方だけ違う選択肢が 2 つ以上あるカード(線4 の補い)": partial}


def every_option(real: Mapping[str, Any], cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """線 6 (ii): 実案件のカード全部の選択肢を 1 つずつ答えて、戻りも書きもされずに消えた答えを数える。"""
    tried = lost = refused = 0
    for c in cards:
        for o in c["選択肢"]:
            runs = {n: [dict(it) if it["id"] in set((c.get("行") or {}).values()) else it for it in rows]
                    for n, rows in real["runs"].items()}
            res = split_cards.apply(runs, [c], {c["鍵"]: o})
            tried += 1
            back = {d["鍵"] for d in res["決めた行"]} | set(res["書くだけの答え"])
            refused += len(res["戻せなかった答え"])
            if c["鍵"] not in back and not res["戻せなかった答え"]:
                lost += 1
    return {"答えた数": tried, "戻せなかった答え(理由つき)": refused, "消えた答え": lost}


def parse_check() -> dict[str, Any]:
    """線 6 (i): 合成の答えの文字(正しい形・全角・揺れ・読めない形)を読む。読めない書き方は全部理由つきで残る。"""
    cards = [{"鍵": f"k{i}", "選択肢": ["1m2", "2m2", split_cards.NONE_OF_THESE, split_cards.DONT_KNOW]}
             for i in range(1, 5)]
    text = "1-3、２－１ 3ー2,4−9\n5-1 x-1 3-1 1 2 ３－"
    got = answers_io.parse_numbered(text, cards)
    tokens = got["読んだ文字の数"]
    return {"文字": text, "読んだ文字の数": tokens, "戻せた答え": len(got["答え"]),
            "戻した文字": len(got["戻した文字"]), "戻せなかった答え": len(got["戻せなかった答え"]),
            "理由ごと": _count([r["理由"] for r in got["戻せなかった答え"]]),
            "答えの無いカード": got["答えの無いカード"],
            "黙って捨てた": tokens - len(got["戻した文字"]) - len(got["戻せなかった答え"])}


def decoy_check() -> dict[str, Any]:
    """線 5: K-66 の細目の囮と言い換えを、3 回の読み(左・右・左)として並べる(合成。実案件を使わない)。"""
    from sameness.decoys import decoy_pairs, paraphrase_pairs

    def item(rid: str, name: str, qty: float | None, unit: str) -> dict[str, Any]:
        return {"id": rid, "工事": name, "何": name, "場所": "室A", "部位": "", "品番": "", "数量": qty, "単位": unit,
                "ページ": 1, "囲み": [100.0, 100.0, 200.0, 140.0], "要素": ["e1", "e2"], "状態": "観測"}

    def run(pair: Any) -> dict[str, Any]:
        unit = pair.unit or "m2"
        lq = pair.left_quantity if pair.left_quantity is not None else 10.0
        rq = pair.right_quantity if pair.right_quantity is not None else 10.0
        runs = {"R1": [item("a", pair.left, lq, unit)], "R2": [item("b", pair.right, rq, unit)],
                "R3": [item("c", pair.left, lq, unit)]}
        return split_cards.find_splits(runs)

    decoys = [p for p in decoy_pairs() if p.level == "細目"]
    name_only, qty_missed, by_kind = [], [], {}
    for p in decoys:
        f = run(p)
        if f["名前だけの違い(辞書で解いた)"]:
            name_only.append(p.種類)
        if p.種類.startswith("5") and not any(c["次元"] == "数量" for c in f["カード"]):
            qty_missed.append(p.種類)
        where = "カード" if f["カード"] else ("カードにできなかった" if f["カードにできなかった組"] else
                                         ("有無だけの割れ" if f["割れた鍵"] else "割れない"))
        by_kind.setdefault(p.種類, {}).setdefault(where, 0)
        by_kind[p.種類][where] += 1
    paraphrases = [p for p in paraphrase_pairs() if p.level == "細目"]
    para_cards = sum(1 for p in paraphrases if run(p)["カード"])
    para_name_only = sum(1 for p in paraphrases if run(p)["名前だけの違い(辞書で解いた)"])
    return {"囮の対(細目)": len(decoys), "線5(i): 名前だけの違いに入った囮": len(name_only),
            "数量違いの対": sum(1 for p in decoys if p.種類.startswith("5")),
            "線5(ii): 数量のカードにならなかった数量違い": len(qty_missed),
            "囮の行き先(種類ごと)": by_kind,
            "言い換えの対(細目)": len(paraphrases), "カードになった言い換え": para_cards,
            "名前だけの違いとして解いた言い換え": para_name_only}


def measure(real: Mapping[str, Any], *, count: int = 10, machine_check: bool = True) -> dict[str, Any]:
    found = real["found"]
    cards = real["ordered"]
    by_dim = found["入れ先ごとの鍵"]
    value_keys = sum(by_dim[d] for d in split_cards.DIMENSIONS)
    carded = sum(c["割れた鍵の数"] for c in cards)
    reasons: dict[str, dict[str, int]] = {}
    for x in found["カードにできなかった組"]:
        r = reasons.setdefault(x["理由"], {"組": 0, "割れた鍵": 0})
        r["組"] += 1
        r["割れた鍵"] += x["割れた鍵の数"]
    reason_keys = sum(r["割れた鍵"] for r in reasons.values())
    reasons_by_dim: dict[str, dict[str, int]] = {}
    for x in found["カードにできなかった組"]:
        d = reasons_by_dim.setdefault(x["次元"], {})
        d[x["理由"]] = d.get(x["理由"], 0) + x["割れた鍵の数"]
    silent = found["割れた鍵"] - (carded + reason_keys + by_dim[split_cards.PRESENCE_ONLY]
                                + by_dim[split_cards.UNIT_NAME_ONLY])
    sources = {s: 0 for s in split_cards.SOURCES}
    for c in cards:
        for o in split_cards.real_options(c):
            for s in c["値の出どころ"][o]:
                sources[s["出どころ"]] += 1
    machine_items = sum(len(v) for v in real["machine"].values())
    scale_items = sum(len(v) for v in real["scale"].values())
    shape = card_shape(cards)
    top = cards[:count]
    out: dict[str, Any] = {
        "材料": "K-61 が保存した P011 匿名化 v4 の全部あり版 3 回(基準の回は 1 回目)",
        "割れた鍵": found["割れた鍵"], "鍵の和": found["鍵の和"], "全部の回に出た鍵": found["全部の回に出た鍵"],
        "割れた鍵の割合(割れた鍵 ÷ 鍵の和)": _ratio(found["割れた鍵"], found["鍵の和"]),
        "入れ先ごとの鍵": by_dim,
        "名前だけの違い(辞書で解いた。カードにしない鍵)": found["名前だけの違い(辞書で解いた)"],
        "値の割れの鍵": value_keys,
        "カードにした鍵": carded, "カード": len(cards),
        "カードの次元ごと": _count([c["次元"] for c in cards]),
        "カードにした鍵の次元ごと": {d: sum(c["割れた鍵の数"] for c in cards if c["次元"] == d)
                             for d in split_cards.DIMENSIONS},
        "カードにできなかった理由(組と鍵)": reasons,
        "カードにできなかった理由(次元ごと、鍵)": reasons_by_dim,
        "線1: どこにも入らなかった鍵": silent,
        "線1: 入れ先の和(カード+できなかった+有無だけ+単位の書き方だけ)": [
            carded, reason_keys, by_dim[split_cards.PRESENCE_ONLY], by_dim[split_cards.UNIT_NAME_ONLY]],
        "線2: カードにした鍵 ÷ 値の割れの鍵": [carded, value_keys, _ratio(carded, value_keys)],
        "選択肢の値の出どころ(数)": sources,
        "機械の値のある項目(3 回分)": machine_items, "縮尺で換算した値のある項目(3 回分)": scale_items,
        "選択肢の実の値の数(カードごと)": _count([len(split_cards.real_options(c)) for c in cards]),
        "線3・4: カードの形": shape,
        "並べ方": {"1つの答えで確定するカード": sum(1 for c in cards if c["1つの答えで確定する行"]),
                "金額": "未取得(原価表なし)。値が決まる行の数で並べた",
                "枠ごとのカード": _count([c["枠"] for c in cards]),
                "上位 10 枚の次元": _count([c["次元"] for c in top]),
                "上位 10 枚の 1 つの答えで確定する": sum(1 for c in top if c["1つの答えで確定する行"])},
        "線5: 囮と言い換え(合成)": decoy_check(),
        "線6(i): 答えの文字を読む(合成)": parse_check(),
        "線6(ii): 実案件の全部の選択肢を 1 つずつ答える": every_option(real, cards),
        "動き(全部のカード)": {p: movement(real, cards, p, machine_check=machine_check) for p in POLICIES},
        "動き(PDF の 10 枚)": {p: movement(real, top, p, machine_check=machine_check) for p in POLICIES[:2]},
        "但し書き": ["答えの方針は合成で、正しさの測定ではない(正解は開いていない)",
                 "選択肢に正しい値が無い割合と、答えを戻したときの正しさは PC 側の手順書で測る",
                 "メーターの見込みは出さない(K-68 B 周 7 のずれの中央値 100% > 20%)"],
    }
    mv = out["動き(全部のカード)"]
    out["線"] = {
        "線1 黙って落とさない": silent == 0,
        "線2 カードにできた割合 0.50 以上": (_ratio(carded, value_keys) or 0) >= LINE2_MIN_SHARE,
        "線3 カードの形": all(v == 0 for k, v in shape.items() if not k.startswith("線4") and "線4 の補い" not in k),
        "線4 名前だけ": shape["線4: 実の値が全部同じカード(名前だけの違い)"] == 0,
        "線5 囮": (out["線5: 囮と言い換え(合成)"]["線5(i): 名前だけの違いに入った囮"] == 0
                  and out["線5: 囮と言い換え(合成)"]["線5(ii): 数量のカードにならなかった数量違い"] == 0),
        "線6 戻す": out["線6(i): 答えの文字を読む(合成)"]["黙って捨てた"] == 0
                  and out["線6(ii): 実案件の全部の選択肢を 1 つずつ答える"]["消えた答え"] == 0,
        "線7 自動確定 0": all(mv[p].get("自動確定", 0) == 0 for p in POLICIES[:2]),
        "線8 動かない答え": all(mv[p]["どれかの欄が変わった項目"] == 0 and mv[p]["3状態 前→後"][0] == mv[p]["3状態 前→後"][1]
                          and mv[p]["割れた鍵 / 鍵の和 前→後"][0] == mv[p]["割れた鍵 / 鍵の和 前→後"][1]
                          for p in POLICIES[2:]),
        "線9 方針 A の未確定の減り 4 以上": mv[POLICIES[0]]["未確定の減り"] >= LINE9_MIN_DROP,
    }
    return out


def main(argv: list[str] | None = None) -> int:
    from benchmarks.make_k70_split_cards import build_real

    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=None)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--pdf-check", type=Path, default=None, help="作った PDF の確かめの結果(JSON)を結果に入れる")
    a = p.parse_args(argv)
    real = build_real(a.runs, a.pdf)
    result = measure(real)
    if a.pdf_check and a.pdf_check.exists():
        result["線10: PDF(実案件の 10 枚)"] = json.loads(a.pdf_check.read_text(encoding="utf-8"))
    result["但し書き(全体)"] = "件数と割合だけ。正解は開いていない。AI は 1 回も呼んでいない"
    text = json.dumps(result, ensure_ascii=False, indent=1, default=sorted)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
