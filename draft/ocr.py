"""OCR の結果を受け取る口(K-64 周 3)。**文字の層が無いページだけ、OCR の語を文字の層の代わりに使う。**

OCR はここでは動かさない(パソコン側の Codex-A が PaddleOCR で作った JSON を受け取る)。受け取れる形は 3 つ:

1. この一本道の形::

    {"ページ": [{"ページ": 1, "幅": 4961, "高さ": 3508,
                 "語": [{"文字": "洋室1", "位置": [x0, y0, x1, y1], "確かさ": 0.98}]}]}

2. PaddleOCR 2.x の結果(1 ページぶん)を ``"結果"`` に入れたもの::

    {"ページ": [{"ページ": 1, "幅": w, "高さ": h, "結果": [[[[x, y], [x, y], [x, y], [x, y]], ["洋室1", 0.98]], ...]}]}

3. PaddleOCR 3.x の結果(``rec_texts`` / ``rec_scores`` / ``rec_polys`` または ``dt_polys`` / ``rec_boxes``)を
   ``"結果"`` に入れたもの。

座標は ``幅``・``高さ``(OCR に掛けた画像の大きさ)から、幅 2000 画素の画像の座標に直す。幅が無ければ直さない
(すでに幅 2000 画素の座標として扱う)。**読めなかった語を作らない。確かさは写すだけで、ふるい落としはしない。**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from draft.pages import WIDTH_PX

#: OCR の JSON のパスを渡す環境変数。
OCR_ENV = "DRAFT_OCR"


def _box(points: Any) -> list[float] | None:
    """四隅の点 [[x, y] x 4] か [x0, y0, x1, y1] を外接矩形にする。"""
    try:
        if len(points) == 4 and all(isinstance(v, (int, float)) for v in points):
            x0, y0, x1, y1 = (float(v) for v in points)
            return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _words_of(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(entry.get("語"), list):
        out = []
        for w in entry["語"]:
            if isinstance(w, Mapping) and str(w.get("文字") or "").strip():
                box = _box(w.get("位置") or [])
                if box:
                    out.append({"文字": str(w["文字"]), "位置": box, "確かさ": w.get("確かさ")})
        return out
    res = entry.get("結果")
    if isinstance(res, Mapping):  # PaddleOCR 3.x
        texts = list(res.get("rec_texts") or [])
        scores = list(res.get("rec_scores") or [])
        polys = res.get("rec_polys") or res.get("dt_polys") or res.get("rec_boxes") or []
        out = []
        for i, text in enumerate(texts):
            box = _box(polys[i]) if i < len(polys) else None
            if box and str(text).strip():
                out.append({"文字": str(text), "位置": box, "確かさ": scores[i] if i < len(scores) else None})
        return out
    if isinstance(res, list):  # PaddleOCR 2.x(1 ページを [[...]] で包んだものも受ける)
        lines = res[0] if len(res) == 1 and isinstance(res[0], list) and res[0] and isinstance(res[0][0], list) \
            and len(res[0][0]) == 2 and isinstance(res[0][0][1], (list, tuple)) else res
        out = []
        for line in lines or []:
            try:
                points, (text, score) = line[0], line[1]
            except (TypeError, ValueError, IndexError):
                continue
            box = _box(points)
            if box and str(text).strip():
                out.append({"文字": str(text), "位置": box, "確かさ": score})
        return out
    return []


def load_ocr(source: str | Path | Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """OCR の JSON を読み、ページごとの語(幅 2000 画素の座標)にする。"""
    payload = source if isinstance(source, Mapping) else json.loads(Path(source).read_text(encoding="utf-8"))
    pages = payload.get("ページ") or payload.get("pages") or []
    out: dict[int, list[dict[str, Any]]] = {}
    for entry in pages:
        if not isinstance(entry, Mapping):
            continue
        n = int(entry.get("ページ") or entry.get("page") or 0)
        if n <= 0:
            continue
        width = entry.get("幅") or entry.get("width")
        scale = WIDTH_PX / float(width) if width else 1.0
        words = []
        for w in _words_of(entry):
            words.append({**w, "位置": [round(v * scale, 1) for v in w["位置"]]})
        words.sort(key=lambda w: (round(w["位置"][1] / 10), w["位置"][0]))
        out[n] = words
    return out


def apply_ocr(pages: Sequence[Any], ocr: Mapping[int, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """**文字の層が空のページだけ** OCR の語を文字の層の代わりに入れる(``PageInfo.text`` を書き換える)。

    文字の層があるページは OCR を使わない(使わなかったページとして数える)。
    """
    used, ignored, empty = [], [], []
    for p in pages:
        words = ocr.get(p.number)
        if words is None:
            continue
        if p.text.strip():
            ignored.append(p.number)
            continue
        if not words:
            empty.append(p.number)
            continue
        p.text = "\n".join(w["文字"] for w in words)
        used.append(p.number)
    return {"OCR を使ったページ": used, "文字の層があるので使わなかったページ": ignored, "OCR に語が無かったページ": empty}


def positioned(ocr: Mapping[int, Sequence[Mapping[str, Any]]], number: int) -> list[list[Any]]:
    """``draft.pages.positioned_words`` と同じ形(``[語, x0, y0, x1, y1]``)にする。"""
    return [[w["文字"], *(round(v) for v in w["位置"])] for w in ocr.get(number, [])]
