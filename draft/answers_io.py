"""答えを戻す口(K-65 の 5)。**AI を呼び直さない。影響範囲だけ数え直す。**

基準は `docs/k65_question_curve_criteria.md` の 5 節(測る前にコミットした)。

applying そのものは K-64 周 4 の `draft.stages.apply_answers` がやる。ここは
**その前後**だけを足す。

1. `answers.json` を読む(形の検査。読めない行は落として理由を残す)。
2. **矛盾を検出する**(3 つだけ。保守的)。
3. 影響範囲(連鎖の閉包)を出し、**そこだけ数え直す。**
4. 答える前と後で変わった項目を一覧にする。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft import chain as chain_mod
from draft.stages import nfkc, room_key

#: 矛盾の型(この 3 つだけ)。
CONTRADICTIONS = ("同じ鍵に違う答え", "入らないと採るの両方", "撤去と新設の両方")

_REMOVE = ("撤去", "解体", "取外", "取り外")
_NEW = ("新設", "取付", "取り付", "設置", "新規")


def load(path: str | Path) -> dict[str, Any]:
    """`answers.json` を読む。

    形: ``{"回答": [{"鍵": "...", "選択肢": "...", "秒": 12, "迷った": false}]}``。
    **鍵と選択肢の無い行は落とし、落とした理由を返す。**(黙って埋めない)
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("回答") if isinstance(raw, Mapping) else raw
    good: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    for i, row in enumerate(rows or (), 1):
        if not isinstance(row, Mapping):
            bad.append({"行": i, "理由": "辞書ではない"})
            continue
        key, choice = nfkc(row.get("鍵")), str(row.get("選択肢") or "")
        if not key:
            bad.append({"行": i, "理由": "鍵が無い"})
            continue
        if not choice:
            bad.append({"行": i, "鍵": key, "理由": "選択肢が無い"})
            continue
        good.append({"鍵": key, "選択肢": choice,
                     "秒": row.get("秒"), "迷った": bool(row.get("迷った")),
                     "図面全体を開いた": bool(row.get("図面全体を開いた"))})
    return {"回答": good, "読めなかった行": bad,
            "答えた数": len(good), "秒の合計": sum(r["秒"] or 0 for r in good)}


def contradictions(answers: Sequence[Mapping[str, Any]], cards: Sequence[Mapping[str, Any]],
                   items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """矛盾を検出する。**見つけた分は再質問に回す。検出できない矛盾があることも報告する。**"""
    by_key = {c["鍵"]: c for c in cards}
    by_id = {it["id"]: it for it in items}
    out: list[dict[str, Any]] = []

    seen: dict[str, str] = {}
    for a in answers:
        prior = seen.get(a["鍵"])
        if prior is not None and prior != a["選択肢"]:
            out.append({"型": "同じ鍵に違う答え", "鍵": a["鍵"], "答え": [prior, a["選択肢"]]})
        seen[a["鍵"]] = a["選択肢"]

    scope: dict[tuple[str, str], set[str]] = {}
    for a in answers:
        card = by_key.get(a["鍵"])
        if card is None:
            continue
        key = (room_key(card.get("室")), nfkc(card.get("部位")))
        if "入らない" in a["選択肢"]:
            scope.setdefault(key, set()).add("入らない")
        if "図面の読みのとおり" in a["選択肢"] or "原本" in a["選択肢"]:
            scope.setdefault(key, set()).add("採る")
    for key, kinds in scope.items():
        if {"入らない", "採る"} <= kinds:
            out.append({"型": "入らないと採るの両方", "室": key[0], "部位": key[1]})

    phase: dict[str, set[str]] = {}
    for a in answers:
        card = by_key.get(a["鍵"])
        if card is None or card.get("型") != "状態":
            continue
        for item_id in card.get("直接") or ():
            if any(w in a["選択肢"] for w in _REMOVE):
                phase.setdefault(item_id, set()).add("撤去")
            if any(w in a["選択肢"] for w in _NEW):
                phase.setdefault(item_id, set()).add("新設")
    for item_id, kinds in phase.items():
        if {"撤去", "新設"} <= kinds:
            out.append({"型": "撤去と新設の両方", "項目": item_id,
                        "場所": nfkc(by_id.get(item_id, {}).get("場所"))})
    return out


def affected(chain: chain_mod.Chain, answers: Sequence[Mapping[str, Any]],
             cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """影響範囲。**ここだけ数え直す(案件全体を読み直さない)。**"""
    by_key = {c["鍵"]: c for c in cards}
    direct: list[str] = []
    for a in answers:
        card = by_key.get(a["鍵"])
        if card is None:
            continue
        direct.extend(card.get("直接") or ())
    direct = list(dict.fromkeys(i for i in direct if i in chain.nodes))
    reached: dict[str, int] = {}
    for a in answers:
        card = by_key.get(a["鍵"])
        if card is None:
            continue
        ids = [i for i in card.get("直接") or () if i in chain.nodes]
        kinds = chain_mod.TRAVERSAL.get(card.get("型"))
        for node, depth in chain.closure(ids, kinds=kinds).items():
            reached.setdefault(node, depth)
    reached = {k: v for k, v in reached.items() if k not in set(direct)}
    return {"直接": direct, "連鎖": sorted(reached),
            "数え直す項目数": len(direct) + len(reached),
            "案件の項目数": len(chain.nodes),
            "数え直した割合": round((len(direct) + len(reached)) / len(chain.nodes), 4) if chain.nodes else None}


_WATCH = ("数量", "単位", "確度", "状態", "根拠の種類", "場所", "部位", "工事")


def snapshot(items: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {it["id"]: {k: it.get(k) for k in _WATCH} for it in items}


def changed(before: Mapping[str, Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """答える前と後で変わった項目の一覧。"""
    out: list[dict[str, Any]] = []
    for it in items:
        old = before.get(it["id"])
        if old is None:
            out.append({"id": it["id"], "変わった欄": "新しく出た項目"})
            continue
        diff = {k: {"前": old.get(k), "後": it.get(k)} for k in _WATCH if old.get(k) != it.get(k)}
        if diff:
            out.append({"id": it["id"], "変わった欄": diff})
    return out


def wrong_answer_detection(answers: Sequence[Mapping[str, Any]], wrong_keys: Sequence[str],
                           found: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """間違えた答えのうち、矛盾の検出が見つけた割合。**0% でもそのまま出す。**"""
    wrong = set(wrong_keys)
    caught: set[str] = set()
    for c in found:
        if c.get("鍵") in wrong:
            caught.add(c["鍵"])
        for item_id in (c.get("項目"),):
            if item_id in wrong:
                caught.add(item_id)
    return {"間違えた答え": len(wrong), "矛盾で見つけた": len(caught),
            "見つけた割合": round(len(caught) / len(wrong), 4) if wrong else None,
            "但し書き": "矛盾の検出は 3 つの型だけなので、見つけられない間違いがある"}
