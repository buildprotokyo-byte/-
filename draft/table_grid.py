"""K-71 作業 3 周 1: **AI が中身を読んだ罫線の表は、罫線を機械が図形の層から読んで台帳に足す。**

基準は `docs/k71_readrate_criteria.md` の周 1(測る前にコミット済み)。

- 対象の表は `draft.readthrough._table_rects` が返す罫線の表(平面図を表と誤認しない守りと、K-72 の図を表と誤認しない守りを通ったもの)。
- **AI が中身を読んだ証拠**: 表の中の文字の層の語のうち、AI の「文字」「数字」の要素(面積の上限 1% 以内。
  印の付け方は `benchmarks/erase_check.py` と同じ)で印が付く語の割合が ``MIN_READ_SHARE`` 以上。
  満たさない表には何も足さない。**AI が読んでいない表を、機械だけで「読めた」にしない**(囮で稼がない)。
- 足すのは、表の中の水平・垂直の直線と矩形のうち、**升目の縁に乗るもの**(罫線)を 1 つずつ。位置はその図形の外接矩形そのまま(広げない)。
  升目の縁に乗らない線(表と誤認された図の中の線など)は足さない(周 1 の結果の後で厳しくした。基準の周 1 の記録を参照)。

K-73 作業 3(b)(基準 `docs/k73_grid_text_criteria.md`、測る前にコミット済み): 証拠を**位置と中身**に厳しくした。
語は、印を付ける AI の「文字」「数字」の要素のうち少なくとも 1 つで、**正規化した語が正規化した AI の「内容」に含まれる**ときだけ数える
(正規化 = NFKC → 空白を除く → casefold)。内容を持たない箱・中身の違う箱では稼げない。``text_match=False`` で前(位置だけ)。

読みの側の直しであって、物差し(面積の上限 1%・余白 3 画素・見本の点の半分)は変えない。
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Mapping, Sequence

#: AI が表の中身を読んだとみなす、表の中の語に AI の文字の要素で印が付いた割合(測る前に決めた)。
MIN_READ_SHARE = 0.5
#: 罫線とみなす直線の向きの差(度)。
AXIS_TOLERANCE_DEG = 1.0
#: 升目の縁に乗るとみなす距離(画素。幅 2000 画素の座標)。
EDGE_TOLERANCE_PX = 2.0
#: 升目の縁と重なる長さの割合(線と縁の短い方に対して)。
EDGE_OVERLAP = 0.5
#: 中身の証拠に数える AI の要素の種類。
TEXT_KINDS = ("文字", "数字")
SOURCE = "機械(罫線の表)"
_SPACE = re.compile(r"\s+")


def normalize(text: Any) -> str:
    """中身の比べ方の正規化: NFKC → 空白をすべて除く → casefold。"""
    return _SPACE.sub("", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def content_matched(words: Sequence[Any], elements: Sequence[Mapping[str, Any]], page_area: float, cap: float) -> list[bool]:
    """語ごとに「AI の文字の中身が一致する」か(K-73 作業 3(b))。

    位置は `erase_check.mark_read` と同じ(面積の上限・余白・見本の点の半分)。その位置で語を覆う要素の
    少なくとも 1 つで、正規化した語が正規化した「内容」に部分文字列として含まれれば一致。
    """
    import numpy as np

    from benchmarks import erase_check as ec

    mg = ec.SETTINGS["印の余白(画素)"]
    need = ec.SETTINGS["印に要る見本の点の割合"]
    boxes, texts = [], []
    for e in elements:
        pos = e.get("位置")
        if not pos or len(pos) != 4:
            continue
        x0, y0 = min(pos[0], pos[2]), min(pos[1], pos[3])
        x1, y1 = max(pos[0], pos[2]), max(pos[1], pos[3])
        if (x1 - x0) * (y1 - y0) > cap * page_area:
            continue
        text = normalize(e.get("内容"))
        if not text:
            continue
        boxes.append((x0 - mg, y0 - mg, x1 + mg, y1 + mg))
        texts.append(text)
    out = []
    B = np.array(boxes).reshape(-1, 4)
    for p in words:
        word = normalize(p.text)
        if not word or not texts:
            out.append(False)
            continue
        pts = p.points
        inside = ((pts[:, None, 0] >= B[None, :, 0]) & (pts[:, None, 0] <= B[None, :, 2])
                  & (pts[:, None, 1] >= B[None, :, 1]) & (pts[:, None, 1] <= B[None, :, 3]))
        covering = np.nonzero(inside.mean(axis=0) >= need)[0]
        out.append(any(word in texts[j] for j in covering))
    return out
CONTENT = "表の罫線(機械が図形の層から読んだ)"


def _axis_aligned(prim: Any) -> bool:
    pts = prim.points
    a, b = pts[0], pts[-1]
    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
    return min(ang, 180 - ang) <= AXIS_TOLERANCE_DEG or abs(ang - 90) <= AXIS_TOLERANCE_DEG


def _cell_edges(cells: Sequence[Sequence[float]], scale: float) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """升目の四角(pt)から、横の縁 (y, x0, x1) と縦の縁 (x, y0, y1)(画素)。"""
    horizontal, vertical = [], []
    for c in cells:
        x0, y0, x1, y1 = (v * scale for v in c)
        horizontal += [(y0, x0, x1), (y1, x0, x1)]
        vertical += [(x0, y0, y1), (x1, y0, y1)]
    return horizontal, vertical


def _on_edges(coord: float, lo: float, hi: float, edges: Sequence[tuple[float, float, float]]) -> bool:
    length = hi - lo
    for e, e0, e1 in edges:
        if abs(coord - e) > EDGE_TOLERANCE_PX:
            continue
        overlap = min(hi, e1) - max(lo, e0)
        if overlap >= EDGE_OVERLAP * max(min(length, e1 - e0), EDGE_TOLERANCE_PX):
            return True
    return False


def is_ruling(prim: Any, horizontal: Sequence[tuple[float, float, float]], vertical: Sequence[tuple[float, float, float]]) -> bool:
    """図形が升目の縁に乗る罫線か(水平・垂直の直線、または 4 辺とも縁に乗る矩形)。"""
    x0, y0, x1, y1 = prim.bbox
    if prim.kind == "直線" and _axis_aligned(prim):
        if (x1 - x0) >= (y1 - y0):
            return _on_edges((y0 + y1) / 2, x0, x1, horizontal)
        return _on_edges((x0 + x1) / 2, y0, y1, vertical)
    if prim.kind == "矩形":
        return (_on_edges(y0, x0, x1, horizontal) and _on_edges(y1, x0, x1, horizontal)
                and _on_edges(x0, y0, y1, vertical) and _on_edges(x1, y0, y1, vertical))
    return False


def grid_elements(page: Any, number: int, elements: Sequence[Mapping[str, Any]],
                  cap: float = 0.01, drawing_guard: bool = True,
                  text_match: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """機械が読んだ罫線の要素と、表ごとの記録(証拠の割合・足した数)を返す。

    ``text_match=True``(既定、K-73)は中身の一致した語の割合で、False は前(位置だけ)の割合で証拠を見る。
    """
    from benchmarks import erase_check as ec
    from draft.readthrough import _inside, _ruled_tables

    tables, _ = _ruled_tables(page, drawing_guard)
    if not tables:
        return [], []
    scale = ec.WIDTH_PX / page.rect.width
    w, h = page.rect.width * scale, page.rect.height * scale
    prims = ec.extract_primitives(page, number)
    ec.mark_exclusions(prims, w, h)
    text_elements = [e for e in elements if e.get("位置") and str(e.get("種類") or "") in TEXT_KINDS]
    ec.mark_read(prims, list(text_elements), w * h, cap)

    added: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for index, table in enumerate(tables, 1):
        rect = table["四角"]
        horizontal, vertical = _cell_edges(table["升目"], scale)
        inside = [p for p in prims if not p.excluded and _inside(p.bbox, [rect], scale)]
        words = [p for p in inside if p.kind == "文字"]
        marked_share = (sum(1 for p in words if p.marked) / len(words)) if words else 0.0
        note = {"表": index, "表の中の語": len(words), "AI の文字で印が付いた語の割合": round(marked_share, 4)}
        share = marked_share
        if text_match:
            matched = content_matched(words, text_elements, w * h, cap)
            share = (sum(matched) / len(words)) if words else 0.0
            note["AI の文字の中身が一致した語の割合"] = round(share, 4)
        if not words or share < MIN_READ_SHARE:
            note["機械が足した罫線"] = 0
            note["足さなかった理由"] = ("AI が表の中身を読んだ証拠が足りない(中身の一致)" if text_match
                                  else "AI が表の中身を読んだ証拠が足りない")
            notes.append(note)
            continue
        count = 0
        for p in inside:
            if is_ruling(p, horizontal, vertical):
                count += 1
                added.append({
                    "id": f"p{number}-罫{index}-{count}",
                    "種類": "線",
                    "内容": CONTENT,
                    "位置": [round(float(v), 2) for v in p.bbox],
                    "確かさ": "読めた",
                    "出どころ": SOURCE,
                })
        note["機械が足した罫線"] = count
        notes.append(note)
    return added, notes


def ledger(page: Any, number: int, elements: Sequence[Mapping[str, Any]],
           cap: float = 0.01, drawing_guard: bool = True,
           text_match: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """台帳 = AI の要素 + 機械が読んだ罫線。記録(表ごとの証拠と足した数)も返す。

    対象の表は `_ruled_tables` の守り(K-72: 図を表と誤認したものを外す)を通ったもの。``drawing_guard=False`` で前の守り。
    """
    added, notes = grid_elements(page, number, elements, cap, drawing_guard, text_match)
    record = {"機械が足した罫線": len(added), "表ごと": notes}
    return list(elements) + added, record
