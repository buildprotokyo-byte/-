"""K-68 C 周 3: 費用の見立てを ①普通のプログラム ②専用の小さなモデル だけで数え直す。

**新しく AI は呼ばない。**既にある回の ``下書き.json``(AI の答えの記録・読み・整理・仕上表の原本)と図面の文字の層だけで数える。
線と数え方は ``docs/k68_c_criteria.md`` の周 3(測る前にコミット)。② は動かせるモデルが無いので全段「未測定」。

使い方::

    python -m benchmarks.k68_cost_recount 図面.pdf 回1/下書き.json 回2/下書き.json ... > 結果.json
"""

from __future__ import annotations

import json
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

UNMEASURED = "未測定"

#: 整理の段をプログラムで置き換える規則(上から順。測る前に決めた。見て直さない)。
KIND_RULES = (
    (r"図面リスト|図面目録|表紙", "表紙・図面リスト"),
    (r"仕上表|仕上げ表", "仕上表"),
    (r"仕様書|特記仕様", "仕様書"),
    (r"凡例", "凡例"),
    (r"建具表", "建具表"),
    (r"展開図", "展開図"),
    (r"平面図", "平面図"),
    (r"詳細図", "詳細図"),
    (r"設備|電気|給排水|空調", "設備図"),
    (r"概要", "概要"),
)
NO_KIND = "決められない"
LINE_AGREE = 0.95
LINE_OVER_DECOY = 0.20
PAD = 3.0
TEXT_KINDS = ("文字", "数字")

#: 費用の目安の単価(ドル / 100 万トークン)。`draft.ai.PRICE_PER_MTOK` と同じ値を使う。
def _price(model: str) -> tuple[float, float]:
    from draft.ai import price_of

    pin, pout, _ = price_of(model)
    return pin, pout


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def kind_by_rule(text: str) -> str:
    for pattern, kind in KIND_RULES:
        if re.search(pattern, unicodedata.normalize("NFKC", text or "")):
            return kind
    return NO_KIND


def stage_costs(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """呼び出しの記録から段ごとの費用の目安(入力・出力に分ける)。未取得の呼び出しは数えない(K-62 の cost.py と同じ)。"""
    out: dict[str, dict[str, float]] = {}
    for r in records:
        s = out.setdefault(r["段"], {"回数": 0, "入力": 0.0, "出力": 0.0})
        s["回数"] += 1
        if r.get("答えの出どころ") == "未取得":
            continue
        pin, pout = _price(r.get("モデル") or "")
        s["入力"] += (r.get("入力トークンの目安") or 0) * pin / 1e6
        s["出力"] += (r.get("出力トークンの目安") or 0) * pout / 1e6
    return out


def organize_agreement(page_texts: Mapping[int, str], ai_kinds: Mapping[int, str], seed: int = 0) -> dict[str, Any]:
    pages = sorted(n for n in ai_kinds if n in page_texts)
    if not pages:
        return {"ページ": 0, "一致": None, "囮": None}
    rule = {n: kind_by_rule(page_texts[n]) for n in pages}
    agree = sum(rule[n] == ai_kinds[n] for n in pages) / len(pages)
    shuffled = [ai_kinds[n] for n in pages]
    random.Random(seed).shuffle(shuffled)
    decoy = sum(rule[n] == k for n, k in zip(pages, shuffled)) / len(pages)
    return {"ページ": len(pages), "一致": round(agree, 4), "囮": round(decoy, 4),
            "囮との差": round(agree - decoy, 4)}


def _joined_words(box: Sequence[float], words: Sequence[tuple[str, Sequence[float]]]) -> str:
    x0, y0, x1, y1 = box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD
    inside = [t for t, b in words if x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1]
    return norm("".join(inside))


def text_matches(content: Any, joined: str) -> bool:
    c = norm(content)
    if not c or not joined:
        return False
    if c == joined:
        return True
    short, long_ = sorted((c, joined), key=len)
    return short in long_ and len(short) >= 0.8 * len(long_)


def reading_share(reading: Mapping[Any, Mapping[str, Any]],
                  words: Mapping[int, Sequence[tuple[str, Sequence[float]]]]) -> dict[str, Any]:
    """読みの要素のうちプログラムで出せる割合(JSON の字数で)。囮は次のページの文字の層。"""
    pages = sorted(int(n) for n in reading)
    with_text = [n for n in pages if words.get(n)]
    nxt = {n: with_text[(i + 1) % len(with_text)] for i, n in enumerate(with_text)} if len(with_text) > 1 else {}
    total = real = decoy = 0
    count = {"要素": 0, "文字・数字": 0, "出せる": 0, "囮で出せる": 0}
    for n in pages:
        for e in (reading.get(n) or reading.get(str(n)) or {}).get("要素", []) or []:
            size = len(json.dumps(e, ensure_ascii=False))
            total += size
            count["要素"] += 1
            if e.get("種類") not in TEXT_KINDS or not e.get("位置") or len(e["位置"]) != 4:
                continue
            count["文字・数字"] += 1
            if text_matches(e.get("内容"), _joined_words(e["位置"], words.get(n, ()))):
                real += size
                count["出せる"] += 1
            if n in nxt and text_matches(e.get("内容"), _joined_words(e["位置"], words.get(nxt[n], ()))):
                decoy += size
                count["囮で出せる"] += 1
    share = real / total if total else None
    dshare = decoy / total if total else None
    return {**count, "出せる割合": None if share is None else round(share, 4),
            "囮": None if dshare is None else round(dshare, 4),
            "囮との差": None if share is None else round(share - dshare, 4)}


def finish_share(rows: Sequence[Mapping[str, Any]], page_texts: Mapping[int, str]) -> dict[str, Any]:
    pages = sorted(page_texts)
    nxt = {n: pages[(i + 1) % len(pages)] for i, n in enumerate(pages)} if len(pages) > 1 else {}
    usable = [r for r in rows if norm(r.get("室")) and norm(r.get("仕上"))]
    if not usable:
        return {"行": 0, "割合": None, "囮": None}
    texts = {n: norm(t) for n, t in page_texts.items()}

    def has(r: Mapping[str, Any], n: int | None) -> bool:
        t = texts.get(n, "") if n is not None else ""
        return norm(r["室"]) in t and norm(r["仕上"]) in t

    real = sum(has(r, r.get("ページ")) for r in usable) / len(usable)
    decoy = sum(has(r, nxt.get(r.get("ページ"))) for r in usable) / len(usable)
    return {"行": len(usable), "割合": round(real, 4), "囮": round(decoy, 4), "囮との差": round(real - decoy, 4)}


def recount(pdf: Path, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    import pymupdf

    from draft.pages import words_in_image

    with pymupdf.open(pdf) as doc:
        texts = {i + 1: doc.load_page(i).get_text("text") for i in range(doc.page_count)}
        words = {i + 1: words_in_image(doc.load_page(i)) for i in range(doc.page_count)}

    runs = []
    for res in results:
        org = {int(n): v.get("種類") for n, v in (res.get("整理") or {}).get("ページ", {}).items()}
        rows = (res.get("仕上表") or {}).get("原本の行") or []
        runs.append({
            "費用": stage_costs(res["AI を呼んだ記録"]["1回ずつ"]),
            "整理": organize_agreement(texts, org),
            "読み": reading_share((res.get("読む") or {}).get("読み") or {}, words),
            "仕上表の原本": finish_share(rows, texts),
        })

    org_ok = all(r["整理"]["一致"] is not None and r["整理"]["一致"] >= LINE_AGREE
                 and r["整理"]["囮との差"] >= LINE_OVER_DECOY for r in runs)
    read_ok = all(r["読み"]["囮との差"] is not None and r["読み"]["囮との差"] >= LINE_OVER_DECOY for r in runs)
    fin_ok = all(r["仕上表の原本"]["割合"] is not None and r["仕上表の原本"]["割合"] >= LINE_AGREE
                 and r["仕上表の原本"]["囮との差"] >= LINE_OVER_DECOY for r in runs)
    verdict = {"整理": org_ok, "通読・読み直し(出力のうち文字・数字)": read_ok, "仕上表の原本": fin_ok, "理解": False}

    for r in runs:
        before = {k: v["入力"] + v["出力"] for k, v in r["費用"].items()}
        after = dict(before)
        if org_ok and "整理" in after:
            after["整理"] = 0.0
        if read_ok:
            share = r["読み"]["出せる割合"] or 0.0
            for stage in ("通読", "読み直し"):
                if stage in r["費用"]:
                    after[stage] = before[stage] - r["費用"][stage]["出力"] * share
        if fin_ok and "仕上表の原本" in after:
            after["仕上表の原本"] = 0.0
        r["① 普通のプログラム"] = {
            "段ごと 前→後": {k: [round(before[k], 2), round(after[k], 2)] for k in before},
            "合計 前→後": [round(sum(before.values()), 2), round(sum(after.values()), 2)],
        }
        r["② 専用の小さなモデル"] = {k: UNMEASURED for k in before}
    return {"線を通ったか(①で置き換えてよいか)": verdict, "回ごと": runs,
            "② 専用の小さなモデル": "未測定(動かせる専用の小さなモデルが無い。費用の数字は出さない)"}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    pdf, paths = Path(args[0]), [Path(p) for p in args[1:]]
    results = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    print(json.dumps(recount(pdf, results), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
