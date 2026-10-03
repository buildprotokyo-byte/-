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

#: 質問の型(この 8 つだけ)。`どの科目か` は K-68 B 周 2 で足した(科目も固定の選択肢で聞くため)。
TYPES = ("工事の有無", "どの室・部位か", "状態", "数量", "まとめ方", "仕様書と図面のどちらを採るか",
         "同じ物か別の物か", "どの科目か")

#: 型ごとの回答時間の見積(秒)。**未較正。**section 7 の本物の人の回答で較正する。
#: `どの科目か` の 20 秒は K-68 B の仮の判断(`工事の有無` と同じ 1 タップの問いとして置いた)。
SECONDS = {"工事の有無": 20, "どの室・部位か": 30, "状態": 20, "数量": 40, "まとめ方": 30,
           "仕様書と図面のどちらを採るか": 30, "同じ物か別の物か": 40, "どの科目か": 20}
SECONDS_CALIBRATED = False

#: 時間の止め線(秒)。**質問数の上限は置かない**(K-60 の 3/5/10 は MODES に残すが既定では使わない)。
STOP_LINES = (300, 600, 900)

#: 画面に出す「見る所」がこのページ数以上だと、資料を通読しないと答えられないので無効。
MAX_PAGES = 4
#: 画面に出す見る所の数(K-68 B 周 4)。見る所が 4 ページ以上の問いは**捨てず**、先頭 3 か所+「他◯か所」で出す。
SHOWN_PAGES = MAX_PAGES - 1


def shown_pages(pages: Sequence[int]) -> dict[str, Any]:
    """見る所を、画面に出す先頭 3 か所と残りに分ける。**残りは消さない**(`他の見る所` に全部残す)。"""
    pages = list(pages)
    rest = pages[SHOWN_PAGES:]
    return {"見る所": pages[:SHOWN_PAGES], "他の見る所": rest,
            "他◯か所": f"他{len(rest)}か所" if rest else None}

NONE_OF_THESE = "どれでもない(現地・設計者に確認する)"
NOT_IN_SCOPE = "この室・部位は今回の工事に入らない"

#: 科目の固定の選択肢(`draft/prompts/工事概略.txt` の 12 科目。公共建築工事内訳書の科目)。
KAMOKU_OPTIONS = ("仮設", "撤去", "木工事", "内装", "塗装", "建具", "金属", "タイル", "家具・器具",
                  "機械設備", "電気設備", "雑")

#: 固定の選択肢(K-68 B 周 2)。**AI が書いた選択肢は使わない。**最後に必ず「どれでもない」を足す。
#: 表は `docs/k68_b_questions_criteria.md` 周 2 で、測る前に決めた。
FIXED_OPTIONS: dict[str, tuple[str, ...]] = {
    "工事の有無": ("ある", "ない", "別の行に含む", "別途"),
    "状態": ("撤去", "新設", "改修(既存を活かす)", "既存のまま(工事しない)"),
    "まとめ方": ("1 行にまとめる", "分けて数える"),
    "どの科目か": KAMOKU_OPTIONS,
}


def fixed_options(type_: str) -> list[str]:
    """型の固定の選択肢+「どれでもない」。固定の型でなければ空。"""
    if type_ not in FIXED_OPTIONS:
        return []
    return [*FIXED_OPTIONS[type_], NONE_OF_THESE]


def _is_unreadable(card: Mapping[str, Any]) -> bool:
    """「読めなかった所」の問い。選択肢は `draft/stages.UNREADABLE_OPTIONS`(これも閉じた固定の選択肢)。"""
    return str(card.get("鍵", "")).startswith("読めない:")

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
        # K-68 B 周 4 からは、カードを作るときに先頭 3 か所+「他◯か所」にするので、ここには来ない。
        out.append(f"画面に出す見る所が {MAX_PAGES} ページ以上(通読しないと答えられない)")
    if card.get("型") in FIXED_OPTIONS and not _is_unreadable(card) and options != fixed_options(card["型"]):
        out.append("固定の選択肢と違う")
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
                spot_check_ids: Sequence[str] = (),
                quantity_sources: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """`draft.stages.question_candidates` の問いをカードにする。

    ``quantity_sources`` を渡すと、`数量` の型の選択肢をその値から作る(K-68 B 周 3。
    `draft.questioning.quantity_sources`)。渡さなければ K-65 の作り方(同じものの値とほかの回の値)。

    **問いを新しく作らない。**型を決め、メーターを付け、無効なものを外すだけ。
    """
    amounts = dict(amounts or {})
    crops = dict(crops or {})
    uncertain = {r["id"]: r for r in classified["項目ごと"]}
    cards: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for q in questions:
        card = _to_card(q, items, uncertain, crops, other_values, quantity_sources)
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
             other_values: Mapping[tuple[str, str, str, str], Sequence[str]] | None = None,
             quantity_sources: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> dict[str, Any] | None:
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
    elif first is not None and nfkc(first.get("科目")) in ("", "未確定", "未取得"):
        type_ = "どの科目か"
    elif first is not None and first.get("数量") is None:
        type_ = "数量"
    elif first is not None and any(w in nfkc(first.get("工事")) for w in ("撤去", "新設", "改修")):
        type_ = "状態"
    else:
        type_ = "工事の有無"
    options = [str(o) for o in q.get("選択肢") or ()]
    if type_ in FIXED_OPTIONS and kind != "読めなかった所":
        # K-68 B 周 2: AI が書いた選択肢を使わず、型の固定の選択肢にする。
        options = fixed_options(type_)
    sources: dict[str, list[dict[str, Any]]] = {}
    if type_ == "数量" and quantity_sources is not None:
        options, sources = quantity_options(quantity_sources.get(first["id"], ()) if first else ())
    elif type_ == "数量":
        options = _quantity_options(first, by_id, other_values.get(_value_key(first), ()) if first else ())
    if not any(NONE_OF_THESE[:4] in o or "分からない" in o for o in options):
        options.append(NONE_OF_THESE)
    all_pages = sorted(_page_numbers(q.get("見る所")))
    shown = shown_pages(all_pages)
    pages = shown["見る所"]
    sig = [s for i in related for s in (uncertain.get(i, {}).get("信号") or ())]
    card = {
        "鍵": q["鍵"], "型": type_, "問い": q["問い"], "選択肢": options,
        "見る所": pages, "ページ": pages[0] if pages else None,
        "他の見る所": shown["他の見る所"], "他◯か所": shown["他◯か所"],
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
    if type_ == "数量" and quantity_sources is not None:
        card["数量の出どころ"] = sources
    return card


def quantity_text(value: float, unit: str) -> str:
    return f"{value:g}{unit}"


def quantity_options(values: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """数量の選択肢(K-68 B 周 3)。**値の小さい順。どれが推しかは書かない。数字は打たせない。**

    同じ値は 1 つの選択肢にまとめ、出どころを全部残す(`数量の出どころ`)。値が 1 つも無ければ選択肢は
    「どれでもない」だけになり、`invalid_reasons` で捨てる(数字を打たせる方へ逃げない)。
    """
    sources: dict[str, list[dict[str, Any]]] = {}
    order: dict[str, float] = {}
    for v in values:
        text = quantity_text(float(v["値"]), nfkc(v.get("単位")))
        sources.setdefault(text, []).append({"出どころ": v["出どころ"], "辿る": dict(v.get("辿る") or {})})
        order[text] = float(v["値"])
    options = sorted(sources, key=lambda t: (order[t], t))
    return options, sources


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


#: 抜き取りの問いの選択肢(`工事の有無` の固定の選択肢+「どれでもない」。K-68 B 周 2 でそろえた。
#: K-65 では最後が「不明」だった)。
PRESENCE_OPTIONS = tuple(fixed_options("工事の有無"))


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
        all_pages = sorted(_page_numbers(
            [p.get("ページ") if isinstance(p, Mapping) else p for p in raw_pages]))
        # K-68 B 周 4: 先頭 max_pages か所+「他◯か所」。**K-65 までは残りを黙って切っていた。**
        pages, rest = all_pages[:max_pages], all_pages[max_pages:]
        key = f"枠:{frame.get('枠')}"
        reason_list = list(unknown.get("理由") or frame.get("理由") or ())
        reasons = "・".join(str(r) for r in reason_list)
        out.append({
            "鍵": key, "型": "工事の有無", "枠の問い": True,
            "問い": f"この案件に「{frame.get('枠')}」の工事はありますか({reasons})",
            "選択肢": list(FRAME_OPTIONS),
            "見る所": pages, "ページ": pages[0] if pages else None,
            "他の見る所": rest, "他◯か所": f"他{len(rest)}か所" if rest else None,
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


#: `工事の有無` の問いにする理由(K-68 B 周 6)。1 枠 1 問にまとめる(同じ枠の有無を 2 回聞かない)。
PRESENCE_REASONS = ("記載が見当たらない", "読めていない頁がある", "資料が足りない")
PAGE_REASON = "候補が複数で決まらない"
QUANTITY_REASON = "数量の根拠が足りない"


def reason_cards(checklist: Mapping[str, Any] | None, items: Sequence[Mapping[str, Any]] = (), *,
                 quantity_sources: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
                 crops: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """工事チェック表の「分からない理由」ごとに問いを作る(K-68 B 周 6)。**AI は呼ばない。**

    - 記載が見当たらない・読めていない頁がある・資料が足りない → `工事の有無`(1 枠 1 問。固定の選択肢)
    - 候補が複数で決まらない → どのページの記載を採るか(候補ページの先頭 3 つ+どれでもない。答えは書くだけ)
    - 数量の根拠が足りない → その枠に振り分けた数量の無い項目ごとに `数量`(周 3 の候補の値。値が無ければ作らない)

    作れなかった理由は `理由ごと` に残す(**黙って落とさない**)。カードは無効の理由を調べてから返す。
    """
    crops = dict(crops or {})
    qsources = dict(quantity_sources or {})
    out: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    frames_def = None
    by_frame: dict[str, list[Mapping[str, Any]]] = {}
    for frame in (checklist or {}).get("枠") or (checklist or {}).get("枠ごと") or ():
        if frame.get("状態") == "確認できた":
            continue
        name = frame.get("枠")
        unknown = frame.get("分からないこと") or {}
        reason_list = [str(r) for r in (unknown.get("理由") or frame.get("理由") or ())]
        raw_pages = unknown.get("候補ページ") or frame.get("候補ページ") or ()
        all_pages = sorted(_page_numbers([p.get("ページ") if isinstance(p, Mapping) else p for p in raw_pages]))
        shown = shown_pages(all_pages)
        pages = shown["見る所"]

        def base(key: str, type_: str, question: str, options: list[str]) -> dict[str, Any]:
            return {
                "鍵": key, "型": type_, "枠の問い": True, "問い": question, "選択肢": options,
                "見る所": pages, "ページ": pages[0] if pages else None,
                "他の見る所": shown["他の見る所"], "他◯か所": shown["他◯か所"],
                "位置": [{"ページ": p, "位置": None} for p in pages],
                "切り抜き": crops.get(key) or ({"ページ": pages[0], "位置": None, "ページ全体": True}
                                           if pages else None),
                "直接": [], "競っている読みの候補": [], "信号": [], "不確実さ": 1.0,
                "科目": name, "工事": None, "品番": None, "室": None, "部位": None,
                "枠の状態": frame.get("状態"), "理由": reason_list,
                "数字の入力": False, "自由記述": False, "推奨": None,
                "図面全体への入口": {"任意": True, "ページ": pages[0] if pages else None},
            }

        def keep(card: dict[str, Any], reason: str) -> None:
            bad = invalid_reasons(card)
            if bad:
                records.append({"枠": name, "理由": reason, "作った鍵": [], "作れなかった理由": bad})
            else:
                out.append(card)
                records.append({"枠": name, "理由": reason, "作った鍵": [card["鍵"]], "作れなかった理由": []})

        presence = [r for r in reason_list if r in PRESENCE_REASONS]
        if presence:
            if "読めていない頁がある" in presence:
                q = f"候補ページに「{name}」の工事の記載がありますか"
            elif "資料が足りない" in presence:
                q = f"資料が足りないまま聞きます。この案件に「{name}」の工事はありますか"
            else:
                q = f"この案件に「{name}」の工事はありますか"
            card = base(f"枠:{name}", "工事の有無", f"{q}({'・'.join(reason_list)})", fixed_options("工事の有無"))
            bad = invalid_reasons(card)
            if not bad:
                out.append(card)
            for r in presence:
                records.append({"枠": name, "理由": r, "作った鍵": [] if bad else [card["鍵"]],
                                "作れなかった理由": bad})
        if PAGE_REASON in reason_list:
            if len(all_pages) < 2:
                records.append({"枠": name, "理由": PAGE_REASON, "作った鍵": [],
                                "作れなかった理由": ["候補ページが 2 つ未満(選ぶ所が無い)"]})
            else:
                options = [f"{p}ページ" for p in pages] + [NONE_OF_THESE]
                keep(base(f"枠:{name}:ページ", "どの室・部位か",
                          f"「{name}」の記載は、どのページのものを採りますか", options), PAGE_REASON)
        if QUANTITY_REASON in reason_list:
            if frames_def is None:
                from draft import work_checklist

                frames_def = work_checklist.load_frames()
                for it in items:
                    if it.get("数量") is None:
                        by_frame.setdefault(work_checklist.frame_of(it, frames_def)["枠"], []).append(it)
            made, no_value, bad_all = [], 0, []
            for it in by_frame.get(name, ()):
                options, sources = quantity_options(qsources.get(it["id"], ()))
                if not options:
                    no_value += 1
                    continue
                page = it.get("ページ")
                key = f"枠:{name}:数量:{it['id']}"
                card = base(key, "数量", f"{page}ページ: {it.get('何') or it.get('工事')}({it.get('場所')})の数量は、どれですか",
                            [*options, NONE_OF_THESE])
                card.update({"見る所": [page] if page else [], "ページ": page, "他の見る所": [], "他◯か所": None,
                             "位置": [{"ページ": page, "位置": it.get("囲み")}],
                             "切り抜き": crops.get(key) or ({"ページ": page, "位置": it.get("囲み")} if page else None),
                             "直接": [it["id"]], "数量の出どころ": sources, "工事": it.get("工事"),
                             "品番": it.get("品番"), "室": it.get("場所"), "部位": it.get("部位"),
                             "不確実さ": 1.0})
                bad = invalid_reasons(card)
                if bad:
                    bad_all.extend(bad)
                    continue
                out.append(card)
                made.append(key)
            why = []
            if no_value:
                why.append(f"数量の無い項目 {no_value} 個に候補の値が無い")
            if not by_frame.get(name):
                why.append("数量の無い項目が理解に見つからない(枠の振り分けが合わない)")
            why.extend(sorted(set(bad_all)))
            records.append({"枠": name, "理由": QUANTITY_REASON, "作った鍵": made, "作れなかった理由": why})
        for r in reason_list:
            if r not in (*PRESENCE_REASONS, PAGE_REASON, QUANTITY_REASON):
                records.append({"枠": name, "理由": r, "作った鍵": [], "作れなかった理由": ["理由が K-67 の 5 つに無い"]})
    return {"カード": out, "理由ごと": records}


#: 見込みを画面に出してよい、ずれの中央値の上限(K-65 基準 6 節・K-68 B 周 7)。
METER_MAX_GAP = 0.20


def meter_display(calibration: Mapping[str, Any] | None) -> dict[str, Any]:
    """メーターの見込み(`決める項目数`)を画面に出すか。**較正の結果が無ければ出さない。**

    ``calibration`` は較正の結果(``{"ずれの中央値": 0.xx, ...}``)。ずれの中央値が 20% を超えたら出さない。
    """
    if not calibration or calibration.get("ずれの中央値") is None:
        return {"出す": False, "理由": "未較正(較正の結果が無い)"}
    gap = float(calibration["ずれの中央値"])
    if gap > METER_MAX_GAP:
        return {"出す": False, "理由": f"較正のずれの中央値 {gap:.0%} が {METER_MAX_GAP:.0%} を超えた"}
    return {"出す": True, "理由": f"較正のずれの中央値 {gap:.0%}"}


def apply_meter_display(cards: Sequence[dict[str, Any]], calibration: Mapping[str, Any] | None) -> dict[str, Any]:
    """カードの `画面に出す見込み` を決める。**並べ方には内部の数字を使い続ける**(画面に出さないだけ)。"""
    shown = meter_display(calibration)
    for c in cards:
        meter = c.setdefault("メーター", {})
        meter["画面に出す見込み"] = dict(meter.get("決める項目数") or {}) if shown["出す"] else None
        meter["見込みを出さない理由"] = None if shown["出す"] else shown["理由"]
    return shown
