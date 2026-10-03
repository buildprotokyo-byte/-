"""K-67 2 節 / K-70 作業2: **工事チェック表。毎回、決まった枠(大枠 18・小枠 5)を全部出す。**

おーちゃんの K-67:「分かった所は構造化し、分からない所は理由と候補ページを付ける。
**理由まで出れば満足度は 100%。**」

だからこの表は**件数を増やすためのものではない。**空の枠も必ず出し、なぜ空なのかを
理由・候補ページ・未確認の項目で説明する。

守ること
--------
1. **枠は毎回全部出す**(K-67 は 16 枠、K-70 で大枠 18・小枠 5)。読めたものが 0 件の枠も出す。
2. **「ない」と言わない。**図面に「なし」と明記されているときだけ `記載あり:なし`。
   全文検索でその枠の語が見つかるのに `記載が見当たらない` と書くことは**させない**
   (見つかったら「読めていない頁がある」の理由を付けて `一部確認` に落とす)。
3. **振り分けの根拠の語を必ず残す。**どの細目 id・科目・語で振り分けたかを書く。
4. 枠は差し替えられるファイル(`draft/vocab/工事の枠_default.json`)。K-67 の 16 枠は
   `draft/vocab/工事の枠_K67_16枠.json` に残した(前後を比べるため)。
5. **K-70**: 小枠のある大枠(建具・電気)では、項目を小枠まで振り分ける。小枠に当たらないものは
   大枠に直接置く(小枠では 語 → 細目 id → 科目 の順)。**状態は小枠ごとに付け、大枠の状態は小枠(と大枠に直接置いた分)から集計する。**
6. **K-70 の迷い**: 住宅設備と給排水衛生の両方に当たる項目は、片方に決めず両方に紐付けて
   「迷い」として記録する(統合しない)。決まりは枠のファイルの `迷い`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sameness.keys import structure_key
from sameness.normalize import flatten
from sameness.terms import default_terms

DEFAULT_FRAMES = Path(__file__).with_name("vocab") / "工事の枠_default.json"

#: 枠の状態。**この 4 つしか使わない。**
CONFIRMED = "確認できた"
PARTIAL = "一部確認"
NOT_FOUND = "記載が見当たらない"
MISSING_SOURCE = "資料が足りない"

#: 「ほぼ確認(未較正)」(K-68 2 番)。**状態ではなく別の表示。**「確認できた」の定義は今のまま。
#: 一部確認の枠のうち、数量と位置を持つ項目が 1 件以上あり、数量の無い項目が半分未満のもの。
#: 的中率はパソコン側で測ってから、どちらを「確認できた」にするか決める。それまでは信じない。
NEARLY_CONFIRMED = "ほぼ確認(未較正)"


def nearly_confirmed(state: str, found: Sequence[Mapping[str, Any]]) -> bool:
    if state != PARTIAL or not found:
        return False
    complete = [f for f in found if f.get("数量") is not None and f["根拠"].get("ページ") and f["根拠"].get("位置")]
    missing_qty = [f for f in found if f.get("数量") is None]
    return bool(complete) and len(missing_qty) * 2 < len(found)


#: 分からないことの理由。**この 5 つしか使わない。**
REASONS = (
    "記載が見当たらない",
    "読めていない頁がある",
    "候補が複数で決まらない",
    "数量の根拠が足りない",
    "資料が足りない",
)

#: 図面に「なし」と明記されているときの書き方(**これ以外で「ない」と言わない**)。
EXPLICIT_NONE = "記載あり:なし"
_NONE_WORDS = ("なし", "無し", "該当なし", "対象外", "既存のまま")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """枠のファイルを丸ごと読む(`枠` と、あれば `迷い`)。"""
    return json.loads(Path(path or DEFAULT_FRAMES).read_text(encoding="utf-8"))


def load_frames(path: str | Path | None = None) -> list[dict[str, Any]]:
    return list(load_config(path)["枠"])


def _subs(frame: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(frame.get("小枠") or ())


def _all_of(frame: Mapping[str, Any], field: str) -> list[str]:
    """大枠とその小枠の `細目`・`科目`・`語` を合わせたもの(大枠への振り分けに使う)。"""
    out = list(frame.get(field) or ())
    for sub in _subs(frame):
        out += list(sub.get(field) or ())
    return out


def _item_text(item: Mapping[str, Any]) -> str:
    return flatten(" ".join(str(item.get(f) or "") for f in ("工事", "工事項目", "摘要", "品名", "科目")))


def _touches(frame: Mapping[str, Any], key: Any, text: str) -> str | None:
    """項目が枠(小枠を含む)の細目 id・科目 id・語のどれかに当たるか。当たればその根拠。"""
    if key.工事の種類 and key.工事の種類 in _all_of(frame, "細目"):
        return f"細目 {key.工事の種類}"
    if key.科目 and key.科目 in _all_of(frame, "科目"):
        return f"科目 {key.科目}"
    for word in _all_of(frame, "語"):
        if flatten(word) and flatten(word) in text:
            return f"語「{word}」"
    return None


def sub_of(item: Mapping[str, Any], frame: Mapping[str, Any], key: Any, text: str) -> dict[str, Any]:
    """大枠の中で小枠を決める。**語 → 細目 id → 科目 の順。どれにも当たらなければ大枠に直接置く。**

    小枠では語を細目 id より先に見る(K-70 の仮の判断)。手がかりの細目 id は粗く、
    たとえば `玄関ドア` も `D01 室内ドア` になる。小枠の語(`玄関ドア`・`サッシ` など)は
    それより細かいので先に見る。語が 2 つ以上の小枠に当たるときは、語では決めず細目 id に回す。
    """
    subs = _subs(frame)
    hits = []
    for sub in subs:
        word = next((w for w in sub.get("語") or () if flatten(w) and flatten(w) in text), None)
        if word:
            hits.append((sub["名前"], word))
    if len(hits) == 1:
        return {"小枠": hits[0][0], "根拠": f"語「{hits[0][1]}」が品名にある"}
    for sub in subs:
        if key.工事の種類 and key.工事の種類 in (sub.get("細目") or ()):
            return {"小枠": sub["名前"], "根拠": f"細目 {key.工事の種類}"}
    for sub in subs:
        if key.科目 and key.科目 in (sub.get("科目") or ()):
            return {"小枠": sub["名前"], "根拠": f"科目 {key.科目}"}
    if hits:
        return {"小枠": None, "根拠": "小枠が決まらない(" + "・".join(h[0] for h in hits) + " の語に当たった)。大枠に直接置いた"}
    return {"小枠": None, "根拠": "小枠の語・細目・科目に当たらない。大枠に直接置いた"}

def frame_of(item: Mapping[str, Any], frames: Sequence[Mapping[str, Any]], terms=None) -> dict[str, Any]:
    """1 項目をどの枠に入れるか。**根拠を一緒に返す。**

    順: ①細目 id(いちばん細かい)②K-66 の辞書の科目 ③枠の語。どれにも当たらなければ `その他`。
    """
    terms = terms or default_terms()
    key = structure_key(item, terms=terms)
    text = _item_text(item)
    for frame in frames:
        if key.工事の種類 and key.工事の種類 in _all_of(frame, "細目"):
            return {"枠": frame["名前"], "根拠": f"細目 {key.工事の種類}", "当て方": "細目 id"}
    for frame in frames:
        if key.科目 and key.科目 in _all_of(frame, "科目"):
            return {"枠": frame["名前"], "根拠": f"科目 {terms.group('科目', key.科目).代表}", "当て方": "科目"}
    for frame in frames:
        for word in _all_of(frame, "語"):
            if flatten(word) and flatten(word) in text:
                return {"枠": frame["名前"], "根拠": f"語「{word}」が品名にある", "当て方": "語"}
    # K-70 2 周目: 中身の決まらない上位の科目(機械設備など)は、語で決まらなかったときだけ使う。
    for frame in frames:
        if key.科目 and key.科目 in (frame.get("科目(語の後)") or ()):
            return {"枠": frame["名前"], "根拠": f"科目 {terms.group('科目', key.科目).代表}(語で決まらなかった)",
                    "当て方": "科目(語の後)"}
    return {"枠": "その他", "根拠": "細目・科目・語のどれにも当たらなかった", "当て方": "受け皿"}


def _page_words(pdf: Path | None, pages: Iterable[int]) -> dict[int, list[tuple[str, list[float]]]]:
    """ページごとの文字と位置。**全文検索と候補ページに使う。**無ければ空。"""
    if pdf is None:
        return {}
    import pymupdf

    from draft.pages import words_in_image

    out: dict[int, list[tuple[str, list[float]]]] = {}
    with pymupdf.open(pdf) as doc:
        for number in pages:
            if number - 1 >= doc.page_count:
                continue
            page = doc.load_page(number - 1)
            # 回転のあるページも画像の座標に合わせる(K-68 C 周 1)。
            out[number] = [(text, [round(v, 1) for v in box]) for text, box in words_in_image(page)]
    return out


def candidate_pages(frame: Mapping[str, Any], words: Mapping[int, Sequence[tuple[str, list[float]]]],
                    limit: int = 5) -> list[dict[str, Any]]:
    """枠の語が出ているページと、きっかけになった語・位置。**多い順。**"""
    hits: dict[int, list[dict[str, Any]]] = {}
    flat_words = [(w, flatten(w)) for w in (frame.get("語") or ()) if flatten(w)]
    for number, page_words in words.items():
        for text, box in page_words:
            flat = flatten(text)
            if not flat:
                continue
            for original, needle in flat_words:
                if needle in flat:
                    hits.setdefault(number, []).append({"語": original, "見つかった文字": text, "位置": box})
                    break
    ranked = sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    return [{"ページ": number, "当たった語の数": len(found), "きっかけ": found[:3]} for number, found in ranked]


def _component_row(
    name: str,
    frame: Mapping[str, Any],
    found: list[dict[str, Any]],
    words: Mapping[int, Sequence[tuple[str, list[float]]]],
    missing_sources: Sequence[str],
    unread_pages: Sequence[int],
) -> dict[str, Any]:
    """1 つの枠(小枠、または小枠の無い大枠、または大枠に直接置いた分)の行。K-67 の決まりのまま。"""
    candidates = candidate_pages(frame, words)
    explicit_none = any(
        any(none_word in w for none_word in _NONE_WORDS)
        for hit in candidates for part in hit["きっかけ"] for w in (part["見つかった文字"],)
    )
    with_position = [f for f in found if f["根拠"].get("ページ") and f["根拠"].get("位置")]
    with_quantity = [f for f in found if f.get("数量") is not None]

    reasons: list[str] = []
    unconfirmed: list[str] = []
    if found:
        state = CONFIRMED if (with_position and with_quantity and len(with_quantity) == len(found)) else PARTIAL
        if len(with_quantity) < len(found):
            reasons.append("数量の根拠が足りない")
            unconfirmed += [f["項目"] for f in found if f.get("数量") is None][:10]
        if len(with_position) < len(found):
            reasons.append("候補が複数で決まらない")
    elif candidates:
        # **語が見つかっているので「記載が見当たらない」と言わせない。**
        state = PARTIAL
        reasons.append("読めていない頁がある")
        touched = [c["ページ"] for c in candidates if c["ページ"] in set(unread_pages)]
        if touched:
            reasons.append("候補が複数で決まらない")
    elif missing_sources:
        state = MISSING_SOURCE
        reasons.append("資料が足りない")
    else:
        state = NOT_FOUND
        reasons.append("記載が見当たらない")
    if missing_sources and state in (NOT_FOUND, PARTIAL):
        reasons.append("資料が足りない")

    return {
        "枠": name,
        "状態": state,
        NEARLY_CONFIRMED: nearly_confirmed(state, found),
        "明記された「なし」": EXPLICIT_NONE if explicit_none else "",
        "分かったこと": found,
        "件数": len(found),
        "数量が入った件数": len(with_quantity),
        "位置が辿れる件数": len(with_position),
        "分からないこと": {
            "理由": sorted(set(reasons), key=REASONS.index),
            "候補ページ": candidates,
            "未確認の項目": unconfirmed,
        },
    }


def aggregate_state(parts: Sequence[str]) -> str:
    """**大枠の状態を小枠(と大枠に直接置いた分)から集計する。**(K-70、測る前に決めた)

    全部 `確認できた` → `確認できた` / 1 つでも `確認できた`・`一部確認` → `一部確認` /
    それ以外で 1 つでも `資料が足りない` → `資料が足りない` / 残りは `記載が見当たらない`。
    """
    if parts and all(p == CONFIRMED for p in parts):
        return CONFIRMED
    if any(p in (CONFIRMED, PARTIAL) for p in parts):
        return PARTIAL
    if any(p == MISSING_SOURCE for p in parts):
        return MISSING_SOURCE
    return NOT_FOUND


def _other_frame(miss: Mapping[str, Any], name: str) -> str | None:
    pair = list(miss.get("枠") or ())
    if name in pair and len(pair) == 2:
        return pair[1] if pair[0] == name else pair[0]
    return None


def build(
    items: Sequence[Mapping[str, Any]],
    *,
    pdf: Path | None = None,
    pages: Sequence[int] = (),
    frames_path: str | Path | None = None,
    missing_sources: Sequence[str] = (),
    unread_pages: Sequence[int] = (),
) -> dict[str, Any]:
    """工事チェック表を作る。**枠のファイルの枠すべて(小枠も)を返す。**

    `missing_sources` は足りない資料の名前(`仕上表なし`・`原価表なし`・`仕様書なし` など)。
    `unread_pages` は読了率が赤のページ(「読めていない頁がある」の理由に使う)。
    """
    config = load_config(frames_path)
    frames = list(config["枠"])
    by_name = {f["名前"]: f for f in frames}
    misses = list(config.get("迷い") or ())
    terms = default_terms()
    words = _page_words(pdf, pages or sorted({int(i.get("ページ") or 0) for i in items if i.get("ページ")}))

    # 大枠ごとに {小枠の名前 or None(大枠に直接): [項目]}
    assigned: dict[str, dict[str | None, list[dict[str, Any]]]] = {
        f["名前"]: {None: [], **{s["名前"]: [] for s in _subs(f)}} for f in frames
    }
    lost: list[dict[str, Any]] = []

    def place(frame_name: str, entry: dict[str, Any], item: Mapping[str, Any], key: Any, text: str) -> None:
        frame = by_name[frame_name]
        if _subs(frame):
            where_sub = sub_of(item, frame, key, text)
            entry = {**entry, "小枠": where_sub["小枠"], "小枠の根拠": where_sub["根拠"]}
            assigned[frame_name][where_sub["小枠"]].append(entry)
        else:
            assigned[frame_name][None].append(entry)

    for number, item in enumerate(items):
        where = frame_of(item, frames, terms)
        key = structure_key(item, terms=terms)
        text = _item_text(item)
        entry = {
            "項目": item.get("工事") or item.get("工事項目") or "",
            "数量": item.get("数量"), "単位": item.get("単位"),
            "状態": item.get("状態") or item.get("区分"),
            "場所": item.get("場所") or item.get("室"),
            "部位": terms.group("部位", key.部位).代表 if key.部位 else None,
            "根拠": {"ページ": item.get("ページ"), "位置": item.get("位置")},
            "確度": item.get("確度"),
            "中科目": terms.group("中科目", key.中科目).代表 if key.中科目 else None,
            "振り分けの根拠": where["根拠"], "当て方": where["当て方"],
        }
        # K-70 の迷い: 住宅設備と給排水衛生の両方に当たる項目は、両方に紐付ける(統合しない)
        other = None
        other_reason = None
        for miss in misses:
            candidate = _other_frame(miss, where["枠"])
            if candidate and candidate in by_name:
                other_reason = _touches(by_name[candidate], key, text)
                if other_reason:
                    other = candidate
                    break
        if other:
            pair = [where["枠"], other]
            entry = {**entry, "迷い": pair, "迷いの根拠": f"{where['枠']}: {where['根拠']} / {other}: {other_reason}"}
            place(where["枠"], entry, item, key, text)
            place(other, {**entry, "振り分けの根拠": other_reason, "当て方": "迷い(両方に紐付け)"}, item, key, text)
            lost.append({"項目の番号": number, "枠": pair, "根拠": entry["迷いの根拠"]})
        else:
            place(where["枠"], entry, item, key, text)

    rows: list[dict[str, Any]] = []
    sub_rows_all: list[dict[str, Any]] = []
    for frame in frames:
        name = frame["名前"]
        subs = _subs(frame)
        if not subs:
            rows.append(_component_row(name, frame, assigned[name][None], words, missing_sources, unread_pages))
            continue
        sub_rows = [
            {**_component_row(s["名前"], s, assigned[name][s["名前"]], words, missing_sources, unread_pages), "大枠": name}
            for s in subs
        ]
        sub_rows_all += sub_rows
        direct = _component_row(name, frame, assigned[name][None], words, missing_sources, unread_pages)
        # 大枠に直接置いた分は、項目があるか、大枠の語が見つかったときだけ集計に入れる
        include_direct = bool(direct["件数"] or direct["分からないこと"]["候補ページ"])
        parts = [r["状態"] for r in sub_rows] + ([direct["状態"]] if include_direct else [])
        state = aggregate_state(parts)
        all_found = direct["分かったこと"] + [f for r in sub_rows for f in r["分かったこと"]]
        reasons = set(direct["分からないこと"]["理由"]) if include_direct else set()
        for r in sub_rows:
            reasons |= set(r["分からないこと"]["理由"])
        if state != NOT_FOUND:
            reasons.discard("記載が見当たらない")
        if state != MISSING_SOURCE and not missing_sources:
            reasons.discard("資料が足りない")
        rows.append({
            "枠": name,
            "状態": state,
            "状態の出し方": "小枠" + (" と大枠に直接置いた分" if include_direct else "") + "から集計",
            NEARLY_CONFIRMED: nearly_confirmed(state, all_found),
            "明記された「なし」": direct["明記された「なし」"],
            "分かったこと": direct["分かったこと"],
            "件数": len(all_found),
            "大枠に直接置いた件数": direct["件数"],
            "数量が入った件数": sum(1 for f in all_found if f.get("数量") is not None),
            "位置が辿れる件数": sum(1 for f in all_found if f["根拠"].get("ページ") and f["根拠"].get("位置")),
            "分からないこと": {
                "理由": sorted(reasons, key=REASONS.index),
                "候補ページ": direct["分からないこと"]["候補ページ"],
                "未確認の項目": direct["分からないこと"]["未確認の項目"],
            },
            "小枠": sub_rows,
        })

    states = {CONFIRMED: 0, PARTIAL: 0, NOT_FOUND: 0, MISSING_SOURCE: 0}
    for row in rows:
        states[row["状態"]] += 1
    sub_states = {CONFIRMED: 0, PARTIAL: 0, NOT_FOUND: 0, MISSING_SOURCE: 0}
    for row in sub_rows_all:
        sub_states[row["状態"]] += 1
    said_none_wrongly = [
        row["枠"] for row in rows
        if row["状態"] == NOT_FOUND and row["分からないこと"]["候補ページ"]
    ] + [
        f"{row['大枠']}/{row['枠']}" for row in sub_rows_all
        if row["状態"] == NOT_FOUND and row["分からないこと"]["候補ページ"]
    ]
    return {
        "但し書き": "下書き(人が直す前提)。「ない」とは言っていない。空の枠は「記載が見当たらない」",
        "枠の数": len(rows),
        "小枠の数": len(sub_rows_all),
        "状態の分布": states,
        "小枠の状態の分布": sub_states,
        f"{NEARLY_CONFIRMED}の枠": sum(1 for row in rows if row[NEARLY_CONFIRMED]),
        f"{NEARLY_CONFIRMED}の注": "一部確認のうち、数量と位置を持つ項目が 1 件以上あり数量の無い項目が半分未満の枠。"
                                   "確認できたとは別に数える。的中率は未測定",
        "足りない資料": list(missing_sources),
        "語が見つかるのに記載が見当たらないとした枠": said_none_wrongly,
        "迷い": {
            "件数": len(lost),
            "決まり": [m.get("決まり") for m in misses],
            "項目": lost,
        },
        "枠": rows,
    }
