"""周8「罫線の無い置き場の決まりを2つ足す」の測定。

基準は `docs/loop_round8_more_rules_criteria.md`(**測る前にコミット済み**)。

周7 は罫線の升目だけを読み、**先に決めた 9 か所に 1 つも届かなかった。**
正解がどれも罫線の外(図の下の見出しと、囲みの中の箇条書き)にあったためである。
この周は**その 2 つの置き場の決まりを足す。**

**読む場所を決まりごとに分けて返す。4 本まとめて数えない。**
**本番の経路には繋がない。図面の中身は標準出力に出さない。**
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf

from benchmarks.measure_maker_drawings import INK_LEVEL, read, render, render_shifted
from benchmarks.measure_rule_placed_reading import (
    MAX_CELL_RATIO,
    _bucket,
    _covers,
    _levels,
    cells,
    rule_segments,
)

#: 囲みと見なす矩形の最小の大きさ(紙に対する割合)。基準の決まり3。
FRAME_MIN_WIDTH_RATIO = 0.15
FRAME_MIN_HEIGHT_RATIO = 0.10

#: 囲みの中を行に切るときの、空きの画素数。基準の決まり3。
ROW_GAP = 3

#: 行として採る最小の高さ(画素)。
MIN_ROW_HEIGHT = 4

#: 表題欄の帯(紙の下端からの割合)と、その上の見出しの帯。基準の決まり4。
#: **1 案件でしか確かめていない。仮の判断として登録する。**
TITLE_BLOCK_RATIO = 0.12
CAPTION_BAND_RATIO = 0.04

Box = tuple[float, float, float, float]


def frames(page: pymupdf.Page) -> list[Box]:
    """**4 辺に墨が続く大きい矩形**(囲み)を返す。升目より大きいものだけ。"""
    horizontal, vertical = rule_segments(page)
    ys = _levels(horizontal)
    xs = _levels(vertical)
    rows = _bucket(horizontal, ys)
    columns = _bucket(vertical, xs)
    width = page.rect.width
    height = page.rect.height
    out: list[Box] = []
    for i in range(len(ys)):
        for i2 in range(i + 1, len(ys)):
            if ys[i2] - ys[i] < height * FRAME_MIN_HEIGHT_RATIO:
                continue
            if ys[i2] - ys[i] > height * 0.9:
                break
            for j in range(len(xs)):
                for j2 in range(j + 1, len(xs)):
                    if xs[j2] - xs[j] < width * FRAME_MIN_WIDTH_RATIO:
                        continue
                    if xs[j2] - xs[j] > width * 0.9:
                        break
                    if not _covers(rows[i], xs[j], xs[j2]):
                        continue
                    if not _covers(rows[i2], xs[j], xs[j2]):
                        continue
                    if not _covers(columns[j], ys[i], ys[i2]):
                        continue
                    if not _covers(columns[j2], ys[i], ys[i2]):
                        continue
                    out.append((xs[j], ys[i], xs[j2], ys[i2]))
    return _keep_outermost(out)


def _keep_outermost(boxes: list[Box]) -> list[Box]:
    """**いちばん外側の囲みだけ残す**(追記1)。

    内側だけ残すと、**罫線の表が入っている囲みは表だけになり、
    同じ囲みの中にある罫線の無い箇条書きが丸ごと落ちる。**
    決まり3 が読みたいのはその箇条書きなので、外側を残し、
    **表の升目に当たるところは行に切るときに除く。**
    """
    out: list[Box] = []
    for box in boxes:
        outer = any(
            other is not box
            and other[0] <= box[0] + 0.5
            and other[1] <= box[1] + 0.5
            and other[2] >= box[2] - 0.5
            and other[3] >= box[3] - 0.5
            and (other[2] - other[0]) * (other[3] - other[1])
            > (box[2] - box[0]) * (box[3] - box[1]) + 1.0
            for other in boxes
        )
        if not outer:
            out.append(box)
    return out


def split_rows(
    image: np.ndarray,
    page: pymupdf.Page,
    box: Box,
    ruled: list[Box] | None = None,
) -> list[Box]:
    """囲みの中を、**横方向の墨の投影**で行に切る(基準の決まり3)。

    `ruled` に罫線の升目を渡すと、**そこは決まり1 が読むので除く**(追記1)。
    """
    scale_x = image.shape[1] / page.rect.width
    scale_y = image.shape[0] / page.rect.height
    x0 = max(int(box[0] * scale_x), 0)
    y0 = max(int(box[1] * scale_y), 0)
    x1 = min(int(box[2] * scale_x), image.shape[1])
    y1 = min(int(box[3] * scale_y), image.shape[0])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return []
    inside = (image[y0:y1, x0:x1] < INK_LEVEL)
    if ruled:
        for cell in ruled:
            cx0 = max(int(cell[0] * scale_x) - x0, 0)
            cy0 = max(int(cell[1] * scale_y) - y0, 0)
            cx1 = min(int(cell[2] * scale_x) - x0, inside.shape[1])
            cy1 = min(int(cell[3] * scale_y) - y0, inside.shape[0])
            if cx1 > cx0 and cy1 > cy0:
                inside[cy0:cy1, cx0:cx1] = False
    inked = inside.sum(axis=1) > 0
    out: list[Box] = []
    start = None
    gap = 0
    for index, has_ink in enumerate(inked):
        if has_ink:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= ROW_GAP:
                if index - gap - start >= MIN_ROW_HEIGHT:
                    out.append(
                        (box[0], y0 / scale_y + start / scale_y,
                         box[2], y0 / scale_y + (index - gap) / scale_y)
                    )
                start = None
                gap = 0
    if start is not None and len(inked) - start >= MIN_ROW_HEIGHT:
        out.append(
            (box[0], y0 / scale_y + start / scale_y,
             box[2], y0 / scale_y + len(inked) / scale_y)
        )
    return out


def caption_band(page: pymupdf.Page) -> Box:
    """表題欄の帯のすぐ上の、見出しの帯(基準の決まり4)。"""
    height = page.rect.height
    bottom = height * (1.0 - TITLE_BLOCK_RATIO)
    return (0.0, bottom - height * CAPTION_BAND_RATIO, page.rect.width, bottom)


def _crop(image: np.ndarray, page: pymupdf.Page, box: Box, pad: float = 1.0) -> np.ndarray:
    """紙の座標の矩形を、周りに `pad` ポイントの余白を足して切り出す。"""
    scale_x = image.shape[1] / page.rect.width
    scale_y = image.shape[0] / page.rect.height
    x0 = max(int((box[0] - pad) * scale_x), 0)
    y0 = max(int((box[1] - pad) * scale_y), 0)
    x1 = min(int((box[2] + pad) * scale_x), image.shape[1])
    y1 = min(int((box[3] + pad) * scale_y), image.shape[0])
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0), dtype=np.uint8)
    return image[y0:y1, x0:x1]


def places(page: pymupdf.Page, image: np.ndarray) -> list[tuple[str, Box]]:
    """**決まりごとに**読む場所を返す。戻りは (決まりの名前, 矩形) の一覧。"""
    ruled = cells(page)
    out: list[tuple[str, Box]] = [("決まり1 罫線の升目", box) for box in ruled]
    for frame in frames(page):
        for row in split_rows(image, page, frame, ruled):
            out.append(("決まり3 囲みの中の行", row))
    out.append(("決まり4 図の下の見出しの帯", caption_band(page)))
    return out


def _pad_for(box: Box, pad_ratio: float) -> float:
    """切り出しの余白。**その矩形の高さに比例させる**(周9 の基準)。"""
    return max(1.0, (box[3] - box[1]) * pad_ratio)


def measure_page(
    engine: Any,
    doc: pymupdf.Document,
    page_index: int,
    *,
    dpi: int,
    seed: int,
    pad_ratio: float = 0.0,
) -> dict[str, Any]:
    page = doc.load_page(page_index)
    image = render(page, dpi)
    spots = places(page, image)

    texts: list[tuple[str, str, float]] = []
    for name, box in spots:
        tile = _crop(image, page, box, _pad_for(box, pad_ratio))
        if tile.size == 0:
            continue
        for text, score in read(engine, tile):
            texts.append((name, text, score))

    shifted_image = render(render_shifted(page, random.Random(seed + page_index)), dpi)
    decoy: list[tuple[str, str, float]] = []
    for name, box in spots:
        tile = _crop(shifted_image, page, box, _pad_for(box, pad_ratio))
        if tile.size == 0:
            continue
        for text, score in read(engine, tile):
            decoy.append((name, text, score))

    by_rule: dict[str, int] = {}
    for name, _ in spots:
        by_rule[name] = by_rule.get(name, 0) + 1

    return {
        "ページ番号": page_index + 1,
        "解像度": dpi,
        "切り出しの余白の割合": pad_ratio,
        "読んだ単位": len(spots),
        "決まりごとの単位の数": by_rule,
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
    parser.add_argument(
        "--pad-ratio",
        type=float,
        default=0.0,
        help="切り出しの余白を、矩形の高さの何倍にするか(周8 は 0、周9 は 1.0)",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--texts", type=Path)
    args = parser.parse_args(argv)

    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    doc = pymupdf.open(args.pdf)
    pages = [
        measure_page(
            engine, doc, int(number) - 1,
            dpi=args.dpi, seed=args.seed, pad_ratio=args.pad_ratio,
        )
        for number in args.pages.split(",")
    ]

    if args.texts:
        args.texts.write_text(
            json.dumps(
                [{k: v for k, v in p.items() if k.startswith("_") or k == "ページ番号"}
                 for p in pages],
                ensure_ascii=False, indent=2,
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
