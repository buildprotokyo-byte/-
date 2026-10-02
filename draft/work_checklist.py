"""K-67 2 節: **工事チェック表。毎回、決まった 16 枠を全部出す。**

おーちゃんの K-67:「分かった所は構造化し、分からない所は理由と候補ページを付ける。
**理由まで出れば満足度は 100%。**」

だからこの表は**件数を増やすためのものではない。**空の枠も必ず出し、なぜ空なのかを
理由・候補ページ・未確認の項目で説明する。

守ること
--------
1. **16 枠は毎回全部出す。**読めたものが 0 件の枠も出す。
2. **「ない」と言わない。**図面に「なし」と明記されているときだけ `記載あり:なし`。
   全文検索でその枠の語が見つかるのに `記載が見当たらない` と書くことは**させない**
   (見つかったら「読めていない頁がある」の理由を付けて `一部確認` に落とす)。
3. **振り分けの根拠の語を必ず残す。**どの細目 id・科目・語で振り分けたかを書く。
4. 枠は差し替えられるファイル(`draft/vocab/工事の枠_default.json`)。
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


def load_frames(path: str | Path | None = None) -> list[dict[str, Any]]:
    raw = json.loads(Path(path or DEFAULT_FRAMES).read_text(encoding="utf-8"))
    return list(raw["枠"])


def frame_of(item: Mapping[str, Any], frames: Sequence[Mapping[str, Any]], terms=None) -> dict[str, Any]:
    """1 項目をどの枠に入れるか。**根拠を一緒に返す。**

    順: ①細目 id(いちばん細かい)②K-66 の辞書の科目 ③枠の語。どれにも当たらなければ `その他`。
    """
    terms = terms or default_terms()
    key = structure_key(item, terms=terms)
    text = flatten(" ".join(str(item.get(f) or "") for f in ("工事", "工事項目", "摘要", "品名", "科目")))
    for frame in frames:
        if key.工事の種類 and key.工事の種類 in (frame.get("細目") or ()):
            return {"枠": frame["名前"], "根拠": f"細目 {key.工事の種類}", "当て方": "細目 id"}
    for frame in frames:
        if key.科目 and key.科目 in (frame.get("科目") or ()):
            return {"枠": frame["名前"], "根拠": f"科目 {terms.group('科目', key.科目).代表}", "当て方": "科目"}
    for frame in frames:
        for word in frame.get("語") or ():
            if flatten(word) and flatten(word) in text:
                return {"枠": frame["名前"], "根拠": f"語「{word}」が品名にある", "当て方": "語"}
    return {"枠": "その他", "根拠": "細目・科目・語のどれにも当たらなかった", "当て方": "受け皿"}


def _page_words(pdf: Path | None, pages: Iterable[int]) -> dict[int, list[tuple[str, list[float]]]]:
    """ページごとの文字と位置。**全文検索と候補ページに使う。**無ければ空。"""
    if pdf is None:
        return {}
    import pymupdf

    from benchmarks import erase_check as ec

    out: dict[int, list[tuple[str, list[float]]]] = {}
    with pymupdf.open(pdf) as doc:
        for number in pages:
            if number - 1 >= doc.page_count:
                continue
            page = doc.load_page(number - 1)
            scale = ec.WIDTH_PX / page.rect.width
            out[number] = [
                (w[4], [round(w[i] * scale, 1) for i in range(4)]) for w in page.get_text("words")
            ]
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


def build(
    items: Sequence[Mapping[str, Any]],
    *,
    pdf: Path | None = None,
    pages: Sequence[int] = (),
    frames_path: str | Path | None = None,
    missing_sources: Sequence[str] = (),
    unread_pages: Sequence[int] = (),
) -> dict[str, Any]:
    """工事チェック表を作る。**16 枠すべてを返す。**

    `missing_sources` は足りない資料の名前(`仕上表なし`・`原価表なし`・`仕様書なし` など)。
    `unread_pages` は読了率が赤のページ(「読めていない頁がある」の理由に使う)。
    """
    frames = load_frames(frames_path)
    terms = default_terms()
    words = _page_words(pdf, pages or sorted({int(i.get("ページ") or 0) for i in items if i.get("ページ")}))

    assigned: dict[str, list[dict[str, Any]]] = {f["名前"]: [] for f in frames}
    for item in items:
        where = frame_of(item, frames, terms)
        key = structure_key(item, terms=terms)
        assigned[where["枠"]].append({
            "項目": item.get("工事") or item.get("工事項目") or "",
            "数量": item.get("数量"), "単位": item.get("単位"),
            "状態": item.get("状態") or item.get("区分"),
            "場所": item.get("場所") or item.get("室"),
            "部位": terms.group("部位", key.部位).代表 if key.部位 else None,
            "根拠": {"ページ": item.get("ページ"), "位置": item.get("位置")},
            "確度": item.get("確度"),
            "中科目": terms.group("中科目", key.中科目).代表 if key.中科目 else None,
            "振り分けの根拠": where["根拠"], "当て方": where["当て方"],
        })

    rows: list[dict[str, Any]] = []
    for frame in frames:
        name = frame["名前"]
        found = assigned[name]
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

        rows.append({
            "枠": name,
            "状態": state,
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
        })

    states = {CONFIRMED: 0, PARTIAL: 0, NOT_FOUND: 0, MISSING_SOURCE: 0}
    for row in rows:
        states[row["状態"]] += 1
    said_none_wrongly = [
        row["枠"] for row in rows
        if row["状態"] == NOT_FOUND and row["分からないこと"]["候補ページ"]
    ]
    return {
        "但し書き": "下書き(人が直す前提)。「ない」とは言っていない。空の枠は「記載が見当たらない」",
        "枠の数": len(rows),
        "状態の分布": states,
        "足りない資料": list(missing_sources),
        "語が見つかるのに記載が見当たらないとした枠": said_none_wrongly,
        "枠": rows,
    }
