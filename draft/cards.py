"""質問カード(K-65 の 3)と、選び方(K-65 の 4)。

基準は `docs/k65_question_curve_criteria.md` の 3・4 節(測る前にコミットした)。

**カードだけで答えられない質問は無効。**(言い換えるか捨てる)
無効の見分けは `invalid_reasons` の 6 つだけで、**カードの見た目では決めない。**

**機械の推す答えはカードに入れない。**(`競っている読みの候補` には値を並べるが、
どれが機械の推しかは書かない。書くと人がそれを選ぶ)

**見込み精度は未較正なので出さない。**構造から出る数字(決める項目数・金額比)だけ出す。

**「決める」は「確定する」ではない(追記 2)。**欄が埋まることと、3 状態が確定に移ることは
別である。P011 で 32 問すべてに答えても、確定に移ったのは 1 項目だった。
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from draft import chain as chain_mod
from draft import uncertainty as unc
from draft.stages import nfkc, room_key

#: 質問の型(この 7 つだけ)。
TYPES = ("工事の有無", "どの室・部位か", "状態", "数量", "まとめ方", "仕様書と図面のどちらを採るか",
         "同じ物か別の物か")

#: 型ごとの回答時間の見積(秒)。**未較正。**section 7 の本物の人の回答で較正する。
SECONDS = {"工事の有無": 20, "どの室・部位か": 30, "状態": 20, "数量": 40, "まとめ方": 30,
           "仕様書と図面のどちらを採るか": 30, "同じ物か別の物か": 40}
SECONDS_CALIBRATED = False

#: 時間の止め線(秒)。**質問数の上限は置かない**(K-60 の 3/5/10 は MODES に残すが既定では使わない)。
STOP_LINES = (300, 600, 900)

#: 「見る所」がこのページ数以上だと、資料を通読しないと答えられないので無効。
MAX_PAGES = 4

NONE_OF_THESE = "どれでもない(現地・設計者に確認する)"
NOT_IN_SCOPE = "この室・部位は今回の工事に入らない"

#: 到達する精度の段階(K-60 の表の許容誤差をそのまま使う)。
PRECISION = (("概算", 0.50, "±15%"), ("通常", 0.75, "±10%"), ("精密", 0.90, "±5%"))


def invalid_reasons(card: Mapping[str, Any]) -> list[str]:
    """無効の理由。**1 つでもあればカードとして出さない。**"""
    out: list[str] = []
    options = [str(o) for o in card.get("選択肢") or ()]
    if card.get("数字の入力"):
        out.append("数字の入力欄がある")
    if card.get("自由記述"):
        out.append("自由記述欄がある")
    if card.get("推奨"):
        out.append("推奨が表示されている")
    if len(options) < 2:
        out.append("選択肢が 2 個未満")
    if not card.get("切り抜き"):
        out.append("切り抜きが無い")
    if len(card.get("見る所") or ()) >= MAX_PAGES:
        out.append(f"見る所が {MAX_PAGES} ページ以上(通読しないと答えられない)")
    return out


def _cause_key(card: Mapping[str, Any]) -> str:
    """同じ原因で割れた項目を 1 問に畳むための鍵。"""
    if card["型"] == "仕様書と図面のどちらを採るか":
        return f"資料:{card.get('室','')}:{card.get('部位','')}"
    if card["型"] == "どの室・部位か":
        return f"室:{card.get('ページ','')}"
    if card["型"] == "同じ物か別の物か":
        return "同じもの:" + ":".join(sorted(card["直接"]))
    return f"{card['型']}:{card.get('工事') or card.get('品番') or card['鍵']}"


def _amount(ids: Sequence[str], amounts: Mapping[str, float]) -> float | None:
    found = [amounts[i] for i in ids if i in amounts]
    return sum(found) if found else None


def _meter(card: dict[str, Any], chain: chain_mod.Chain, amounts: Mapping[str, float],
           total: float | None) -> dict[str, Any]:
    decided = chain_mod.decided_by(chain, card["直接"], card_type=card["型"])
    direct, chained = decided["直接"], decided["連鎖"]
    a_direct = _amount(direct, amounts)
    a_chain = _amount(chained, amounts)
    seconds = SECONDS.get(card["型"], 30)
    meter: dict[str, Any] = {
        "決める項目数": {"直接": len(direct), "連鎖": len(chained), "合計": len(direct) + len(chained)},
        "回答時間の見積(秒)": seconds,
        "回答時間の見積は較正済みか": SECONDS_CALIBRATED,
        "見込み精度の変化": "出さない(未較正。構造から出る数字だけを出す)",
        "但し書き": "「決める」は欄が埋まることで、3 状態の「確定」になることではない"
                 "(K-65 追記 2: 32 問答えて確定に移ったのは 1 項目)",
    }
    if total:
        meter["決める金額"] = {"直接": a_direct, "連鎖": a_chain,
                           "総額比": round(((a_direct or 0) + (a_chain or 0)) / total, 4)}
    else:
        meter["決める金額"] = "未取得(原価表なし)。金額ではなく項目数で並べている"
    card["連鎖"] = decided
    return meter


def build_cards(questions: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
                chain: chain_mod.Chain, classified: Mapping[str, Any], *,
                amounts: Mapping[str, float] | None = None, total: float | None = None,
                crops: Mapping[str, Any] | None = None,
                other_values: Mapping[tuple[str, str, str, str], Sequence[str]] | None = None,
                spot_check_ids: Sequence[str] = ()) -> dict[str, Any]:
    """`draft.stages.question_candidates` の問いをカードにする。

    **問いを新しく作らない。**型を決め、メーターを付け、無効なものを外すだけ。
    """
    amounts = dict(amounts or {})
    crops = dict(crops or {})
    uncertain = {r["id"]: r for r in classified["項目ごと"]}
    cards: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for q in questions:
        card = _to_card(q, items, uncertain, crops, other_values)
        if card is None:
            dropped.append({"鍵": q["鍵"], "理由": "型が決まらなかった"})
            continue
        card["メーター"] = _meter(card, chain, amounts, total)
        bad = invalid_reasons(card)
        if bad:
            dropped.append({"鍵": card["鍵"], "型": card["型"], "理由": "・".join(bad)})
            continue
        cards.append(card)
    for card in spot_check_cards(spot_check_ids, items, crops):
        card["メーター"] = _meter(card, chain, amounts, total)
        if not invalid_reasons(card):
            cards.append(card)
    merged, folded = _fold(cards)
    return {
        "カード": merged,
        "型ごと": {t: sum(1 for c in merged if c["型"] == t) for t in TYPES},
        "捨てたカード": dropped,
        "畳んだカード": folded,
        "但し書き": ["機械の推す答えはカードに入れていない",
                 "見込み精度は未較正なので出していない",
                 "回答時間の見積は未較正(型ごとの固定値)"],
    }


def _value_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return unc.item_key(item)


def _to_card(q: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
             uncertain: Mapping[str, Mapping[str, Any]], crops: Mapping[str, Any],
             other_values: Mapping[tuple[str, str, str, str], Sequence[str]] | None = None) -> dict[str, Any] | None:
    by_id = {it["id"]: it for it in items}
    other_values = dict(other_values or {})
    kind = q.get("種類")
    related = list(q.get("関係する項目") or ())
    first = by_id.get(related[0]) if related else None
    if kind == "原本との違い":
        type_ = "仕様書と図面のどちらを採るか"
    elif kind == "読めなかった所":
        type_ = "工事の有無"
    elif first is not None and len(related) > 1 and first.get("同じもの"):
        type_ = "同じ物か別の物か"
    elif first is not None and room_key(first.get("場所")) in ("", "未確定", "未取得"):
        type_ = "どの室・部位か"
    elif first is not None and first.get("数量") is None:
        type_ = "数量"
    elif first is not None and any(w in nfkc(first.get("工事")) for w in ("撤去", "新設", "改修")):
        type_ = "状態"
    else:
        type_ = "工事の有無"
    options = [str(o) for o in q.get("選択肢") or ()]
    if type_ == "数量":
        options = _quantity_options(first, by_id, other_values.get(_value_key(first), ()) if first else ())
    if not any(NONE_OF_THESE[:4] in o or "分からない" in o for o in options):
        options.append(NONE_OF_THESE)
    pages = sorted(_page_numbers(q.get("見る所")))
    sig = [s for i in related for s in (uncertain.get(i, {}).get("信号") or ())]
    card = {
        "鍵": q["鍵"], "型": type_, "問い": q["問い"], "選択肢": options,
        "見る所": pages, "ページ": pages[0] if pages else None,
        "位置": list(q.get("位置") or ()),
        "切り抜き": crops.get(q["鍵"]) or ({"ページ": pages[0], "位置": (q.get("位置") or [{}])[0].get("位置")}
                                      if pages else None),
        "直接": related,
        "競っている読みの候補": _rivals(first, by_id) if first is not None else [],
        "信号": sorted(set(sig)),
        "不確実さ": round(len(set(sig)) / len(unc.SIGNALS), 4) if sig else 0.0,
        "科目": q.get("科目"), "工事": q.get("工事"), "品番": q.get("品番"),
        "室": (first or {}).get("場所"), "部位": (first or {}).get("部位"),
        "数字の入力": False, "自由記述": False, "推奨": None,
        "図面全体への入口": {"任意": True, "ページ": pages[0] if pages else None},
    }
    return card


def _page_numbers(values: Any) -> set[int]:
    """ページ番号の集合。**保存した JSON を読み直すと鍵が文字になる**ので数字の文字も読む。"""
    out: set[int] = set()
    for v in values or ():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.add(int(v))
            continue
        text = nfkc(v)
        if text.isdigit():
            out.add(int(text))
    return out


def _quantity_options(item: Mapping[str, Any] | None, by_id: Mapping[str, Mapping[str, Any]],
                      other_values: Sequence[str] = ()) -> list[str]:
    """数量の選択肢。**3 回の読みの値・機械の値・縮尺で換算した値から選ぶ形。数字は打たせない。**

    いま手元にあるのは 1 回の読みなので、同じものと結ばれた項目の値を並べる。
    **値が 1 つも無いときは選択肢が 1 個になり、`invalid_reasons` で無効になる**
    (数字を打たせる方へ逃げない)。
    """
    if item is None:
        return []
    values: list[str] = []
    unit = nfkc(item.get("単位"))
    for cand in [item, *[by_id[i] for i in item.get("同じもの") or () if i in by_id]]:
        if cand.get("数量") is None:
            continue
        text = f"{cand['数量']:g}{nfkc(cand.get('単位')) or unit}"
        if text not in values:
            values.append(text)
    for text in other_values:
        if text and text not in values:
            values.append(text)
    return values


def _rivals(item: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """競っている読みの候補。**どれが機械の推しかは書かない。**"""
    out: list[dict[str, Any]] = []
    for cand in [item, *[by_id[i] for i in item.get("同じもの") or () if i in by_id]]:
        out.append({"ページ": cand.get("ページ"), "読み取った値": cand.get("読み取った値"),
                    "工事": cand.get("工事"), "数量": cand.get("数量"), "単位": cand.get("単位")})
    return out


def _fold(cards: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """同じ原因で割れた項目を 1 問にまとめる。"""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for c in cards:
        groups.setdefault(_cause_key(c), []).append(c)
    merged: list[dict[str, Any]] = []
    folded: list[dict[str, Any]] = []
    for key, group in groups.items():
        head = dict(group[0])
        if len(group) > 1:
            head["まとめた鍵"] = [c["鍵"] for c in group[1:]]
            head["原因"] = key
            folded.append({"原因": key, "まとめた数": len(group)})
        merged.append(head)
    return merged, folded


def priority(card: Mapping[str, Any], *, has_cost: bool) -> float:
    """優先度 = 連鎖で決める金額 × 不確実さ ÷ 回答時間。

    金額が無い案件では金額の代わりに項目数を使う(報告にそう書く)。
    不確実さが 0 のときは**抜き取りでなければ最下位**にする(聞く理由が無い)。
    """
    meter = card.get("メーター") or {}
    seconds = max(1, int(meter.get("回答時間の見積(秒)") or 30))
    if has_cost and isinstance(meter.get("決める金額"), Mapping):
        a = meter["決める金額"]
        size = (a.get("直接") or 0) + (a.get("連鎖") or 0)
    else:
        size = meter.get("決める項目数", {}).get("合計", 0)
    unsure = card.get("不確実さ") or 0.0
    if card.get("抜き取り"):
        unsure = max(unsure, 1 / len(unc.SIGNALS))
    return size * unsure / seconds


def order(cards: Sequence[Mapping[str, Any]], *, how: str = "連鎖の金額順", has_cost: bool = False,
          seed: int = 65) -> list[dict[str, Any]]:
    """並べる。``how`` は `連鎖の金額順` か `ランダム`。"""
    pool = [dict(c) for c in cards]
    if how == "ランダム":
        random.Random(seed).shuffle(pool)
    else:
        pool.sort(key=lambda c: (-priority(c, has_cost=has_cost), c["鍵"]))
    for i, c in enumerate(pool, 1):
        c["番号"] = f"Q{i}"
        c["優先度"] = round(priority(c, has_cost=has_cost), 6)
    return pool


def cumulative(cards: Sequence[Mapping[str, Any]], *, total: float | None = None,
               item_total: int | None = None) -> list[dict[str, Any]]:
    """累積のメーター。**ここまで答えると総額の何 % が確定か。**

    原価表が無い案件では金額の % を出さず、項目数の % を出す(「金額ではない」と書く)。
    同じ項目を 2 回数えないよう、確定した id を集合で持つ。
    """
    seen: set[str] = set()
    amount = 0.0
    seconds = 0
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(cards, 1):
        meter = c.get("メーター") or {}
        ids = set(c.get("連鎖", {}).get("直接") or ()) | set(c.get("連鎖", {}).get("連鎖") or ())
        new = ids - seen
        seen |= ids
        if total and isinstance(meter.get("決める金額"), Mapping):
            a = meter["決める金額"]
            whole = (a.get("直接") or 0) + (a.get("連鎖") or 0)
            amount += whole * (len(new) / len(ids) if ids else 0)
        seconds += int(meter.get("回答時間の見積(秒)") or 30)
        share = (amount / total) if total else ((len(seen) / item_total) if item_total else None)
        rows.append({
            "問数": i, "決めた項目数": len(seen),
            "決める項目の割合": round(share, 4) if share is not None else None,
            "割合の中身": "金額" if total else "項目数(金額ではない)",
            "回答時間の見積(秒)": seconds,
            "届いた精度": _reached(share),
            "止め線": [f"{s // 60}分" for s in STOP_LINES if seconds <= s],
        })
    return rows


def _reached(share: float | None) -> str:
    if share is None:
        return "未取得"
    out = "届いていない"
    for name, line, tol in PRECISION:
        if share >= line:
            out = f"{name}({tol})"
    return out


def within(rows: Sequence[Mapping[str, Any]], seconds: int) -> int:
    """止め線 ``seconds`` に入る問数。"""
    return sum(1 for r in rows if r["回答時間の見積(秒)"] <= seconds)


#: 抜き取りの問いの選択肢(`工事の有無` の型の閉じた選択肢。基準 3 節の型の表から)。
PRESENCE_OPTIONS = ("ある", "ない", "別の行に含む", "別途", "不明")


def spot_check_cards(ids: Sequence[str], items: Sequence[Mapping[str, Any]],
                     crops: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """確定の項目にも問いを作る(抜き取り)。**確度が高いのに外れた分を拾うため。**

    もとの問いの候補は「決められなかった所」しか作らないので、確定の項目には
    1 つも問いが無い。**印を付けるだけでは抜き取りは 1 件も出ない**(K-65 で実測)。
    そこでここだけは問いを作る。**選択肢は `工事の有無` の閉じた 5 つに固定**で、
    機械の読みをなぞらない(なぞると「はい」を押すだけの問いになる)。
    """
    by_id = {it["id"]: it for it in items}
    crops = dict(crops or {})
    out: list[dict[str, Any]] = []
    for item_id in ids:
        it = by_id.get(item_id)
        if it is None:
            continue
        pages = sorted(_page_numbers([it.get("ページ")]))
        key = f"抜き取り:{item_id}"
        out.append({
            "鍵": key, "型": "工事の有無", "抜き取り": True,
            "問い": f"{it.get('ページ')}ページのこの場所の工事は、どれですか",
            "選択肢": list(PRESENCE_OPTIONS),
            "見る所": pages, "ページ": pages[0] if pages else None,
            "位置": [{"ページ": it.get("ページ"), "位置": it.get("囲み")}],
            "切り抜き": crops.get(key) or ({"ページ": pages[0], "位置": it.get("囲み")} if pages else None),
            "直接": [item_id], "競っている読みの候補": [], "信号": [], "不確実さ": 0.0,
            "科目": it.get("科目"), "工事": it.get("工事"), "品番": it.get("品番"),
            "室": it.get("場所"), "部位": it.get("部位"),
            "数字の入力": False, "自由記述": False, "推奨": None,
            "図面全体への入口": {"任意": True, "ページ": pages[0] if pages else None},
        })
    return out


#: 枠の問いの選択肢(`工事の有無` の閉じた 5 つ。基準 3 節の型の表から)。
FRAME_OPTIONS = PRESENCE_OPTIONS


def frame_cards(checklist: Mapping[str, Any] | None, *, max_pages: int = MAX_PAGES - 1,
                crops: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """K-67 の工事チェック表から問いを作る(K-65 の 0)。

    **これが必要な理由(K-65 で実測)。**項目から作る問いの 9 割は
    「仕様書と図面のどちらを採るか」で、**仕上表の原本が無い版では質問が 29 問から 5 問へ減った。**
    資料が欠けるほど質問が減る、つまり**いちばん分かっていない案件でいちばん黙る**という
    逆向きの動きである。枠の問いは原本に依らないので、資料が欠けても残る。

    枠の問いは項目を 1 つも決めない(枠の有無を決める)。**だから影響小では黙らせない。**
    """
    crops = dict(crops or {})
    out: list[dict[str, Any]] = []
    for frame in (checklist or {}).get("枠") or (checklist or {}).get("枠ごと") or ():
        if frame.get("状態") == "確認できた":
            continue
        unknown = frame.get("分からないこと") or {}
        raw_pages = unknown.get("候補ページ") or frame.get("候補ページ") or ()
        pages = sorted(_page_numbers(
            [p.get("ページ") if isinstance(p, Mapping) else p for p in raw_pages]))[:max_pages]
        key = f"枠:{frame.get('枠')}"
        reason_list = list(unknown.get("理由") or frame.get("理由") or ())
        reasons = "・".join(str(r) for r in reason_list)
        out.append({
            "鍵": key, "型": "工事の有無", "枠の問い": True,
            "問い": f"この案件に「{frame.get('枠')}」の工事はありますか({reasons})",
            "選択肢": list(FRAME_OPTIONS),
            "見る所": pages, "ページ": pages[0] if pages else None,
            "位置": [{"ページ": p, "位置": None} for p in pages],
            "切り抜き": crops.get(key) or ({"ページ": pages[0], "位置": None, "ページ全体": True}
                                       if pages else None),
            "直接": [], "競っている読みの候補": [], "信号": [], "不確実さ": 1.0,
            "科目": frame.get("枠"), "工事": None, "品番": None, "室": None, "部位": None,
            "枠の状態": frame.get("状態"), "理由": reason_list,
            "数字の入力": False, "自由記述": False, "推奨": None,
            "図面全体への入口": {"任意": True, "ページ": pages[0] if pages else None},
        })
    return out
