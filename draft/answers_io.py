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


#: 固定の選択肢の答えが下書きに何をするか(K-68 B 周 2)。**表に無い答えは戻さない(理由を残す)。**
#: - `決める`: その項目を人の回答で決める(状態「観測」・確度「高」・根拠「人の回答」。K-64 周 4 と同じ)。
#:   値の欄があればその値にする。
#: - `外す`: 内訳から外す印を付ける(**消さない**。外した理由を残す)。
#: - `書くだけ`: 人の回答を書くだけで、項目は決めない。
FIXED_EFFECTS: dict[str, dict[str, tuple[str, dict[str, str]]]] = {
    "工事の有無": {"ある": ("決める", {}), "ない": ("外す", {}),
              "別の行に含む": ("外す", {}), "別途": ("外す", {})},
    "状態": {"撤去": ("決める", {"区分": "撤去"}), "新設": ("決める", {"区分": "新設"}),
           "改修(既存を活かす)": ("決める", {"区分": "改修"}), "既存のまま(工事しない)": ("外す", {})},
    "まとめ方": {"1 行にまとめる": ("書くだけ", {}), "分けて数える": ("書くだけ", {})},
    "どの科目か": {k: ("決める", {"科目": k}) for k in (
        "仮設", "撤去", "木工事", "内装", "塗装", "建具", "金属", "タイル", "家具・器具",
        "機械設備", "電気設備", "雑")},
}

#: 「どれでもない」を K-64 の答えの口に渡すときの書き方(**「分からない」と同じく何も決めない**)。
DONT_KNOW_TEXT = "分からない(現地・設計者に確認する)"


def quantity_of(choice: str, card: Mapping[str, Any]) -> tuple[float, str] | None:
    """`数量` のカードの選択肢の文字から値と単位を取る。**カードの選択肢に無い文字は受け取らない。**"""
    import re

    if choice not in (card.get("選択肢") or ()):
        return None
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(.*)$", nfkc(choice))
    if not m:
        return None
    return float(m.group(1)), m.group(2).strip()


def _decide(it: dict[str, Any], text: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """人の回答で 1 項目を決める。`draft.stages.apply_answers` の中の決め方と同じ(K-64 周 4)。"""
    old = {k: it.get(k) for k in ("数量", "単位", "確度", "状態", "区分", "科目")}
    it["人の回答"] = text
    it["状態"] = "観測"
    it["確度"] = "高"
    it["根拠の種類"] = "人の回答"
    for k, v in fields.items():
        it[k] = float(v) if k == "数量" else nfkc(v)
    it.pop("確度の上限", None)
    return {"項目": it["id"], "前": old, "後": {k: it.get(k) for k in old}}


def apply(understanding: dict[str, Any], finish: dict[str, Any], cards: Sequence[Mapping[str, Any]],
          answers: Mapping[str, str]) -> dict[str, Any]:
    """カードの答えを下書きに戻す(K-68 B 周 2)。**AI は呼ばない。**

    固定の選択肢の型(`工事の有無`・`状態`・`まとめ方`・`どの科目か`)の答えは、ここで
    `FIXED_EFFECTS` の表のとおりに戻す。それ以外(仕上の問い・読めなかった所・室の問いなど)は、
    K-64 周 4 の `draft.stages.apply_answers` にそのまま渡す。

    **「どれでもない」は何も決めない**(K-64 の「分からない」と同じ扱いで渡す)。
    戻せなかった答えは `戻せなかった答え` に理由と一緒に残す(**黙って捨てない**)。
    """
    from draft import cards as cards_mod
    from draft.stages import apply_answers

    by_key = {c["鍵"]: c for c in cards}
    by_id = {it["id"]: it for it in understanding["項目"]}
    passthrough: dict[str, Any] = {}
    decided: list[dict[str, Any]] = []
    removed: list[str] = []
    recorded: list[str] = []
    refused: list[dict[str, Any]] = []
    for key, choice in answers.items():
        card = by_key.get(key)
        type_ = (card or {}).get("型")
        fixed = card is not None and type_ in cards_mod.FIXED_OPTIONS and not cards_mod._is_unreadable(card)
        if choice == cards_mod.NONE_OF_THESE:
            if key.startswith(("項目:", "仕上:")):
                passthrough[key] = DONT_KNOW_TEXT
            else:
                recorded.append(key)
            continue
        if type_ == "数量" and (key.startswith("項目:") or (card or {}).get("枠の問い")):
            # K-68 B 周 3: 数量の値を選んだら、その項目の数量と単位をその値にする(人の回答で決める)。
            # 周 6: 工事チェック表の理由から作った数量の問い(`枠:◯:数量:項目`)も同じ。項目はカードの `直接`。
            value = quantity_of(choice, card)
            target = by_id.get(((card or {}).get("直接") or [key.split(":", 1)[1]])[0])
            if value is None or target is None:
                refused.append({"鍵": key, "理由": "数量の選択肢に無い答え" if value is None else "項目が無い"})
                continue
            decided.append(_decide(target, choice, {"数量": value[0], **({"単位": value[1]} if value[1] else {})}))
            continue
        if not fixed and key.startswith("枠:"):
            # 周 6: 「どのページの記載を採るか」など、枠の問いの答えは書くだけ(項目を決めない)。
            if choice in ((card or {}).get("選択肢") or ()):
                recorded.append(key)
            else:
                refused.append({"鍵": key, "理由": "枠の問いの選択肢に無い答え"})
            continue
        if not fixed:
            passthrough[key] = choice
            continue
        effect = FIXED_EFFECTS.get(type_, {}).get(choice)
        if effect is None:
            refused.append({"鍵": key, "理由": "固定の選択肢に無い答え"})
            continue
        what, fields = effect
        targets = [by_id[i] for i in ([key.split(":", 1)[1]] if key.startswith(("項目:", "抜き取り:")) else ())
                   if i in by_id]
        if not targets:
            # 枠の問いなど、項目を持たない問い。**答えを書くだけ。**
            recorded.append(key)
            continue
        if key.startswith("抜き取り:"):
            # 抜き取りは確定の項目への問い。**答えで項目を書き換えない**(食い違いだけ残す)。
            for it in targets:
                it["抜き取りの回答"] = choice
            recorded.append(key)
            continue
        for it in targets:
            if what == "決める":
                decided.append(_decide(it, choice, fields))
            elif what == "外す":
                it["人の回答"] = choice
                it["外す"] = f"人の回答: {choice}"
                it["根拠の種類"] = "人の回答"
                removed.append(it["id"])
            else:
                it["人の回答"] = choice
                recorded.append(key)
    stage = apply_answers(understanding, finish, passthrough)
    # K-64 の口に渡して戻らなかった答え(選択肢に無い文字など)。**黙って捨てない。**「分からない」は数えない。
    back = set(stage.get("戻した鍵") or ())
    for key, choice in passthrough.items():
        if key not in back and choice != DONT_KNOW_TEXT:
            refused.append({"鍵": key, "理由": "K-64 の口が受け取らなかった答え"})
    return {"固定の選択肢で決めた項目": decided, "固定の選択肢で外した項目": removed,
            "書くだけの答え": recorded, "戻せなかった答え": refused,
            "K-64 の口に渡した答え": len(passthrough), "K-64 の口": stage}


def contradictions(answers: Sequence[Mapping[str, Any]], cards: Sequence[Mapping[str, Any]],
                   items: Sequence[Mapping[str, Any]], *,
                   other_runs: Sequence[Sequence[Mapping[str, Any]]] = (),
                   checklist: Mapping[str, Any] | None = None, types: str = "全部") -> list[dict[str, Any]]:
    """矛盾を検出する。**見つけた分は再質問に回す。検出できない矛盾があることも報告する。**

    K-65 の 3 型(同じ鍵に違う答え・入らないと採るの両方・撤去と新設の両方)に、K-68 B 周 5 で 4 型を足した
    (`外したのに同じ室・部位で採った`・`3回とも読んだ物を外した`・`枠はないと答えたが読みにある`・
    `同じ工事に2つの数量`)。``types="K-65"`` で K-65 の 3 型だけにする(比べるため)。
    **項目の値は 1 つも書き換えない。**
    """
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
    scope_keys: dict[tuple[str, str], set[str]] = {}
    for a in answers:
        card = by_key.get(a["鍵"])
        if card is None:
            continue
        key = (room_key(card.get("室")), nfkc(card.get("部位")))
        if "入らない" in a["選択肢"]:
            scope.setdefault(key, set()).add("入らない")
            scope_keys.setdefault(key, set()).add(a["鍵"])
        if "図面の読みのとおり" in a["選択肢"] or "原本" in a["選択肢"]:
            scope.setdefault(key, set()).add("採る")
            scope_keys.setdefault(key, set()).add(a["鍵"])
    for key, kinds in scope.items():
        if {"入らない", "採る"} <= kinds:
            # 鍵たち: K-68 B 周 5 で足した(どの答えを再質問に回すか)。
            out.append({"型": "入らないと採るの両方", "室": key[0], "部位": key[1],
                        "鍵たち": sorted(scope_keys[key])})

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
    if types == "K-65":
        return out
    already = {(c["室"], c["部位"]) for c in out if c["型"] == "入らないと採るの両方"}
    out.extend(c for c in _more_contradictions(answers, by_key, by_id, other_runs, checklist)
               if not (c["型"] == "外したのに同じ室・部位で採った" and (c["室"], c["部位"]) in already))
    return out


#: K-68 B 周 5 で足した矛盾の型(K-65 の `CONTRADICTIONS` 3 型とは別に持つ)。
CONTRADICTIONS_ADDED = ("外したのに同じ室・部位で採った", "3回とも読んだ物を外した", "枠はないと答えたが読みにある",
                        "同じ工事に2つの数量")

#: 「外す」答え(K-68 B 周 5)。**選択肢の文字そのものと比べる**(「図面の読みのとおり: 既存のまま」のように、
#: 仕上の名前の中に「既存のまま」が入る選択肢を、外す答えと取り違えないため)。
_OUT_EXACT = ("ない", "既存のまま(工事しない)", "この室・部位は今回の工事に入らない")
_OUT_WORDS: tuple[str, ...] = ()
#: 何も決めない答え・外しも採りもしない答え。
_NEUTRAL_WORDS = ("どれでもない", "分からない", "別の行に含む", "別途")


def _is_out(choice: str) -> bool:
    return choice in _OUT_EXACT or any(w in choice for w in _OUT_WORDS)


def _is_take(card: Mapping[str, Any], choice: str) -> bool:
    if _is_out(choice) or any(w in choice for w in _NEUTRAL_WORDS):
        return False
    if card.get("型") == "まとめ方" or str(card.get("鍵", "")).startswith("読めない:"):
        return False
    return True


def _more_contradictions(answers: Sequence[Mapping[str, Any]], by_key: Mapping[str, Mapping[str, Any]],
                         by_id: Mapping[str, Mapping[str, Any]],
                         other_runs: Sequence[Sequence[Mapping[str, Any]]],
                         checklist: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """K-68 B 周 5 で足した 4 型。"""
    from draft import uncertainty as unc

    out: list[dict[str, Any]] = []
    last = {a["鍵"]: str(a["選択肢"]) for a in answers}

    # 1. 外したのに同じ室・部位で採った(すべての型の答えで)。
    groups: dict[tuple[str, str], dict[str, list[str]]] = {}
    for key, choice in last.items():
        card = by_key.get(key)
        if card is None or card.get("枠の問い"):
            continue
        room = room_key(card.get("室"))
        if room in ("", "未確定", "未取得"):
            continue
        g = groups.setdefault((room, nfkc(card.get("部位"))), {"外す": [], "採る": []})
        if _is_out(choice):
            g["外す"].append(key)
        elif _is_take(card, choice):
            g["採る"].append(key)
    for (room, part), g in groups.items():
        if g["外す"] and g["採る"] and set(g["外す"]) != set(g["採る"]):
            out.append({"型": "外したのに同じ室・部位で採った", "室": room, "部位": part,
                        "鍵たち": sorted({*g["外す"], *g["採る"]})})

    # 2. 3 回とも読んだ物を外した。
    if other_runs:
        seen_keys = [{unc.agreement_item_key(it) for it in run} for run in other_runs]
        for key, choice in last.items():
            card = by_key.get(key)
            if card is None or not _is_out(choice):
                continue
            read = [i for i in card.get("直接") or () if i in by_id
                    and all(unc.agreement_item_key(by_id[i]) in keys for keys in seen_keys)]
            if read:
                out.append({"型": "3回とも読んだ物を外した", "鍵": key, "項目たち": read[:10],
                            "ほかの回": len(other_runs)})

    # 3. 枠はないと答えたが読みにある。
    frames = {f"枠:{f.get('枠')}": f for f in (checklist or {}).get("枠") or ()}
    for key, choice in last.items():
        card = by_key.get(key)
        if card is None or not card.get("枠の問い") or choice != "ない":
            continue
        frame = frames.get(key)
        if frame is not None and (frame.get("件数") or len(frame.get("分かったこと") or ())):
            out.append({"型": "枠はないと答えたが読みにある", "鍵": key,
                        "読みにある項目の数": frame.get("件数") or len(frame.get("分かったこと") or ())})

    # 4. 同じ工事に 2 つの数量(同じ回で K-66 の鍵が同じ項目に、違う値を答えた)。
    values: dict[tuple[Any, ...], dict[str, str]] = {}
    for key, choice in last.items():
        card = by_key.get(key)
        if card is None or card.get("型") != "数量" or any(w in choice for w in _NEUTRAL_WORDS):
            continue
        for i in (card.get("直接") or ())[:1]:
            if i in by_id:
                values.setdefault(unc.agreement_item_key(by_id[i]), {})[key] = choice
    for k, got in values.items():
        if len(set(got.values())) > 1:
            out.append({"型": "同じ工事に2つの数量", "鍵たち": sorted(got), "答え": sorted(set(got.values()))})
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
    flagged = flagged_keys(found)
    caught = flagged & wrong
    right = {a["鍵"] for a in answers} - wrong
    false = flagged & right
    return {"間違えた答え": len(wrong), "矛盾で見つけた": len(caught),
            "見つけた割合": round(len(caught) / len(wrong), 4) if wrong else None,
            "間違えていない答え": len(right), "誤って挙げた": len(false),
            "誤って挙げた割合": round(len(false) / len(right), 4) if right else None,
            "但し書き": "矛盾の検出で見つけられない間違いがある(型に当たらない間違い)"}


def flagged_keys(found: Sequence[Mapping[str, Any]]) -> set[str]:
    """矛盾の検出が再質問に回す鍵。"""
    out: set[str] = set()
    for c in found:
        if c.get("鍵"):
            out.add(c["鍵"])
        out.update(c.get("鍵たち") or ())
        if c.get("項目"):
            out.add(f"項目:{c['項目']}")
    return out
