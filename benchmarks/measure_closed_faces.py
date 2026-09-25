"""周1-4「閉じた輪郭の内と外で切る」の測定。

基準は `docs/loop_round1d_closed_face_criteria.md`(**測る前にコミット済み**)。

**作り直していない。**閉じた面を作るのは `axes/image_axis/pdf_room_outlines.py` の
`find_room_outlines()` で、**この周はそれに囮の対照を掛けるだけ**である。

**本番の経路には繋がない。図面の中身は返さない。件数だけを返す。**
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import pymupdf

from axes.image_axis.pdf_room_outlines import find_room_outlines
from axes.image_axis.pdf_vector_symbols import DrawingScale
from benchmarks.measure_cluster_reading import (
    MIN_TABLE_MATCH_LENGTH,
    Word,
    collect_words,
    normalize,
    table_cell_texts,
)

Polygon = tuple[tuple[float, float], ...]


def _inside(point: tuple[float, float], polygon: Polygon) -> bool:
    """点が多角形の内側か(奇偶の判定)。"""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            at = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < at:
                inside = not inside
    return inside


def _shift(polygon: Polygon, size: tuple[float, float], rng: random.Random) -> Polygon:
    """**形と大きさを保ったまま**、紙の上のでたらめな位置へ動かす(囮)。"""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    dx = rng.uniform(0.0, max(size[0] - width, 1.0)) - min(xs)
    dy = rng.uniform(0.0, max(size[1] - height, 1.0)) - min(ys)
    return tuple((p[0] + dx, p[1] + dy) for p in polygon)


def _count(polygons: list[Polygon], words: list[Word], cells: set[str]) -> tuple[int, int]:
    """(ちょうど 1 つ入っていた面、2 つ以上入っていた面)を返す。"""
    decided = 0
    undecided = 0
    for polygon in polygons:
        hits = 0
        for word in words:
            text = normalize(word.text)
            if len(text) < MIN_TABLE_MATCH_LENGTH or text not in cells:
                continue
            if _inside(word.centre, polygon):
                hits += 1
        if hits == 1:
            decided += 1
        elif hits > 1:
            undecided += 1
    return decided, undecided


def measure_page(
    pdf_path: str | Path, page_index: int, *, denominator: float, seed: int = 20260925
) -> dict[str, Any]:
    outlines = find_room_outlines(
        pdf_path,
        page_index,
        DrawingScale(denominator, "測定用"),
        min_sqm=0.01,
        max_sqm=100000.0,
        min_width_mm=1.0,
    )
    polygons = [tuple(o.polygon_pt) for o in outlines]

    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        words = collect_words(page)
        size = (page.rect.x1 - page.rect.x0, page.rect.y1 - page.rect.y0)
        cells = table_cell_texts(doc, "仕上")

    decided, undecided = _count(polygons, words, cells)
    rng = random.Random(seed)
    shifted = [_shift(p, size, rng) for p in polygons]
    fake_decided, fake_undecided = _count(shifted, words, cells)

    return {
        "ページ番号": page_index + 1,
        "縮尺の分母": denominator,
        "文字の断片": len(words),
        "閉じた面": len(polygons),
        "実装が名前を付けた面": sum(1 for o in outlines if o.name),
        "決まった面(本物)": decided,
        "2つ以上入った面(本物)": undecided,
        "決まった面(囮)": fake_decided,
        "2つ以上入った面(囮)": fake_undecided,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True, help="ページ番号:縮尺の分母 をカンマ区切りで")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    result = {"ページごと": []}
    for spec in args.pages.split(","):
        number, denominator = spec.split(":")
        result["ページごと"].append(
            measure_page(args.pdf, int(number) - 1, denominator=float(denominator))
        )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
