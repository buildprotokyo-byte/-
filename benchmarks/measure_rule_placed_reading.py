"""周7「製図の決まりで読む場所を先に決めてから読む」の測定。

基準は `docs/loop_round7_rule_placed_reading_criteria.md`(**測る前にコミット済み**、
追記1 も測る前)。

周6 は紙を格子に切って**墨のある区画を全部**読んだ(紙を画像として端から読む)。
この周は**罫線そのものから升目を作り、升目だけを読む**(製図の決まりの側)。

**本番の経路には繋がない。図面の中身は標準出力に出さない。**
"""

from __future__ import annotations

import argparse
import json
import random
from bisect import bisect_left
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf

from benchmarks.measure_maker_drawings import read, render, render_shifted

#: 罫線と見なす線分の最短の長さ(ポイント)。
#: **表の中の縦の仕切りは、1 行の高さ(この紙では約 11pt)しかない。**
#: だから短いものまで拾い、**4 辺に墨が続いているか**のほうで選り分ける。
MIN_RULE_LENGTH = 6.0

#: 線分が水平・垂直と見なせるずれ(ポイント)。
STRAIGHT_TOLERANCE = 1.0

#: 同じ 1 本の罫線とまとめる座標の近さ(ポイント)。
RULE_MERGE = 2.0

#: 升目として採る最小の大きさ(ポイント)。これより小さいものは罫線の交差の粒。
MIN_CELL_WIDTH = 18.0
MIN_CELL_HEIGHT = 7.0

#: 升目として採る最大の大きさ(紙に対する割合)。紙ぜんぶを 1 升目と数えない。
MAX_CELL_RATIO = 0.6

#: 升目を画像から切り出すときの余白(ポイント)。
CELL_PADDING = 1.0

#: 升目の辺の罫線に許す途切れ(ポイント)。これより大きく切れていたら辺ではない。
EDGE_GAP = 2.0


Segment = tuple[float, float, float]  # (一定の座標, 始まり, 終わり)


def rule_segments(page: pymupdf.Page) -> tuple[list[Segment], list[Segment]]:
    """罫線を**線分のまま**集める。戻りは (横線, 縦線)。

    **文字は使わない。**図形の線分だけから、水平・垂直で十分に長いものを拾う。
    線分のままにするのは、**升目の 4 辺に本当に墨が引いてあるか**を後で確かめるため。
    """
    horizontal: list[Segment] = []
    vertical: list[Segment] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
            elif item[0] == "re":
                box = item[1]
                if box.width >= MIN_RULE_LENGTH:
                    horizontal.append((box.y0, box.x0, box.x1))
                    horizontal.append((box.y1, box.x0, box.x1))
                if box.height >= MIN_RULE_LENGTH:
                    vertical.append((box.x0, box.y0, box.y1))
                    vertical.append((box.x1, box.y0, box.y1))
                continue
            else:
                continue
            dx = abs(end.x - start.x)
            dy = abs(end.y - start.y)
            if dy <= STRAIGHT_TOLERANCE and dx >= MIN_RULE_LENGTH:
                horizontal.append(
                    ((start.y + end.y) / 2.0, min(start.x, end.x), max(start.x, end.x))
                )
            elif dx <= STRAIGHT_TOLERANCE and dy >= MIN_RULE_LENGTH:
                vertical.append(
                    ((start.x + end.x) / 2.0, min(start.y, end.y), max(start.y, end.y))
                )
    return horizontal, vertical


def _levels(segments: list[Segment]) -> list[float]:
    """近い座標を 1 本の罫線にまとめ、その代表の座標を返す。"""
    out: list[float] = []
    for value, _, _ in sorted(segments):
        if out and value - out[-1] <= RULE_MERGE:
            continue
        out.append(value)
    return out


def _bucket(segments: list[Segment], levels: list[float]) -> list[list[tuple[float, float]]]:
    """線分を、まとめた罫線ごとの入れ物に振り分ける。"""
    out: list[list[tuple[float, float]]] = [[] for _ in levels]
    for value, start, end in segments:
        index = bisect_left(levels, value)
        for candidate in (index - 1, index, index + 1):
            if 0 <= candidate < len(levels) and abs(levels[candidate] - value) <= RULE_MERGE:
                out[candidate].append((start, end))
                break
    for spans in out:
        spans.sort()
    return out


def _covers(spans: list[tuple[float, float]], low: float, high: float) -> bool:
    """その罫線が、low〜high の範囲を**実際に覆っているか**。

    覆っているとは、**その範囲の端から端まで墨が続いている**こと。
    途切れていれば升目の辺ではない。
    """
    reach = low
    for start, end in spans:
        if start > reach + EDGE_GAP:
            return reach >= high - EDGE_GAP
        reach = max(reach, end)
        if reach >= high - EDGE_GAP:
            return True
    return reach >= high - EDGE_GAP


#: 1 つの升目が跨いでよい罫線の本数の上限。表の升目は隣どうしの罫線で囲まれる。
MAX_SPAN = 8


def cells(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """**4 辺すべてに罫線が引かれている**最小の矩形を升目として返す。

    隣り合う罫線の組み合わせをすべて取ると、表でないところ(姿図の中の線と、
    離れたところの縦線の組み合わせ)まで升目になってしまう。
    **4 辺に墨が続いていることを確かめる**のが、罫線の表の升目の定義である。

    紙には短い線が無数にあるので、**すぐ隣の罫線どうしが辺になるとは限らない。**
    そこで各交点から外へ広げ、**4 辺がそろう最小の矩形**を 1 つだけ採る。
    """
    horizontal, vertical = rule_segments(page)
    ys = _levels(horizontal)
    xs = _levels(vertical)
    rows = _bucket(horizontal, ys)
    columns = _bucket(vertical, xs)
    width = page.rect.width
    height = page.rect.height

    # 罫線 i が、縦線 j と j+1 のあいだを覆っているか。
    hedge = [
        [_covers(rows[i], xs[j], xs[j + 1]) for j in range(len(xs) - 1)]
        for i in range(len(ys))
    ]
    # 縦線 j が、罫線 i と i+1 のあいだを覆っているか。
    vedge = [
        [_covers(columns[j], ys[i], ys[i + 1]) for j in range(len(xs))]
        for i in range(len(ys) - 1)
    ]

    out: list[tuple[float, float, float, float]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for i in range(len(ys) - 1):
        for j in range(len(xs) - 1):
            found = None
            for i2 in range(i + 1, min(i + 1 + MAX_SPAN, len(ys))):
                if not all(vedge[k][j] for k in range(i, i2)):
                    break
                height_pt = ys[i2] - ys[i]
                if height_pt > height * MAX_CELL_RATIO:
                    break
                for j2 in range(j + 1, min(j + 1 + MAX_SPAN, len(xs))):
                    if not all(hedge[i][k] for k in range(j, j2)):
                        break
                    width_pt = xs[j2] - xs[j]
                    if width_pt > width * MAX_CELL_RATIO:
                        break
                    if height_pt < MIN_CELL_HEIGHT or width_pt < MIN_CELL_WIDTH:
                        continue
                    if not all(hedge[i2][k] for k in range(j, j2)):
                        continue
                    if not all(vedge[k][j2] for k in range(i, i2)):
                        continue
                    found = (i, j, i2, j2)
                    break
                if found:
                    break
            if found and found not in seen:
                seen.add(found)
                out.append((xs[found[1]], ys[found[0]], xs[found[3]], ys[found[2]]))
    return out


def _crop(image: np.ndarray, page: pymupdf.Page, box: tuple[float, float, float, float]) -> np.ndarray:
    """紙の座標の矩形を、画像から切り出す。"""
    scale_x = image.shape[1] / page.rect.width
    scale_y = image.shape[0] / page.rect.height
    x0 = max(int((box[0] - CELL_PADDING) * scale_x), 0)
    y0 = max(int((box[1] - CELL_PADDING) * scale_y), 0)
    x1 = min(int((box[2] + CELL_PADDING) * scale_x), image.shape[1])
    y1 = min(int((box[3] + CELL_PADDING) * scale_y), image.shape[0])
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0), dtype=np.uint8)
    return image[y0:y1, x0:x1]


def measure_page(
    engine: Any,
    doc: pymupdf.Document,
    page_index: int,
    *,
    dpi: int,
    seed: int,
) -> dict[str, Any]:
    """1 ページぶん。升目だけを読み、位置を動かした紙の同じ升目を囮にする。"""
    page = doc.load_page(page_index)
    boxes = cells(page)
    image = render(page, dpi)

    texts: list[tuple[int, str, float]] = []
    for index, box in enumerate(boxes):
        tile = _crop(image, page, box)
        if tile.size == 0:
            continue
        for text, score in read(engine, tile):
            texts.append((index, text, score))

    shifted = render_shifted(page, random.Random(seed + page_index))
    shifted_image = render(shifted, dpi)
    decoy: list[tuple[int, str, float]] = []
    for index, box in enumerate(boxes):
        tile = _crop(shifted_image, page, box)
        if tile.size == 0:
            continue
        for text, score in read(engine, tile):
            decoy.append((index, text, score))

    return {
        "ページ番号": page_index + 1,
        "解像度": dpi,
        "罫線(横)": len(_levels(rule_segments(page)[0])),
        "罫線(縦)": len(_levels(rule_segments(page)[1])),
        "読んだ升目": len(boxes),
        "本物から返った文字の数": len(texts),
        "本物のうち3文字以上": sum(1 for _, t, _ in texts if len(t) >= 3),
        "囮から返った文字の数": len(decoy),
        "囮のうち3文字以上": sum(1 for _, t, _ in decoy if len(t) >= 3),
        "_本物": texts,
        "_囮": decoy,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--texts", type=Path, help="読めた文字の書き出し先(共有フォルダ)")
    args = parser.parse_args(argv)

    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    doc = pymupdf.open(args.pdf)
    pages = [
        measure_page(engine, doc, int(number) - 1, dpi=args.dpi, seed=args.seed)
        for number in args.pages.split(",")
    ]

    if args.texts:
        args.texts.write_text(
            json.dumps(
                [{k: v for k, v in p.items() if k.startswith("_") or k == "ページ番号"}
                 for p in pages],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    counts = {"ページごと": [
        {k: v for k, v in p.items() if not k.startswith("_")} for p in pages
    ]}
    text = json.dumps(counts, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
