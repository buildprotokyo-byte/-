"""周12: **囲いの選び方(面積の窓)が、面が室にならない原因か**を測る。

**この道具は数えるだけで、実装の既定値は変えない。**窓は引数で渡す。

**先に合成の紙で数え方を確かめる**(周11 の反省。数え方を先に決めても、
その数え方が実際に何を数えているかを確かめないと意味を取り違える)。

**落とす代償は、図面自身が室名を印字している面が落ちた数で数える。**
**この数え方は代償を少なめに数える**(印字の無い室は名前を持たない)。
**少なく見えたときに喜ばない。**
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.measure_height_destination import (  # noqa: E402
    ceiling_notes,
    land,
    scatter,
    settled,
)
from axes.image_axis.pdf_room_outlines import find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402

#: 測る面積の上限(平方メートル)。**200 が今の既定値。**
CAPS_SQM = (200.0, 100.0, 50.0, 30.0, 15.0)

FALLBACK_DENOMINATOR = 50.0


def _scale(pdf_path: Path, page_index: int) -> DrawingScale:
    found = extract_scale(pdf_path, page_index)
    if found is not None:
        return found
    return DrawingScale(
        denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
    )


def build_whole_floor_sheet(path: Path) -> None:
    """**合成の紙**(線1 用)。

    3 つの室が並び、**そのあいだの仕切りを描かない**間取り。
    外周だけが閉じているので、**間取り全体が 1 つの面**になる。
    室名を 3 つ印字してある。**実図面は使わない。**
    """
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    # 外周(1/50 で 1 pt ≒ 17.64mm。500x300pt ≒ 8.8m x 5.3m ≒ 46 ㎡)
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    # 仕切りは「途中まで」しか描かない = 閉じないので面が割れない
    shape.draw_line(pymupdf.Point(220, 50), pymupdf.Point(220, 120))
    shape.draw_line(pymupdf.Point(390, 50), pymupdf.Point(390, 120))
    shape.finish(width=1)
    shape.commit()
    for text, x in (("ヘヤイチ", 110), ("ヘヤニ", 280), ("ヘヤサン", 450)):
        page.insert_text((x, 220), text, fontsize=9)
    document.save(path)
    document.close()


def build_split_sheet(path: Path) -> None:
    """**合成の紙**(線1 用、比べる相手)。同じ間取りを**仕切りで割った**もの。"""
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    shape.draw_line(pymupdf.Point(220, 50), pymupdf.Point(220, 350))
    shape.draw_line(pymupdf.Point(390, 50), pymupdf.Point(390, 350))
    shape.finish(width=1)
    shape.commit()
    for text, x in (("ヘヤイチ", 110), ("ヘヤニ", 280), ("ヘヤサン", 450)):
        page.insert_text((x, 220), text, fontsize=9)
    document.save(path)
    document.close()


def check_definition(tmp_dir: Path) -> dict:
    """線1: **合成の紙で、間取り全体の面がいまの窓を通ってしまうか。**

    通ってしまうことが示せて、かつ上限を下げるとその面だけが落ちるなら、
    実図面を測ってよい。
    """
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    whole = tmp_dir / "whole_floor.pdf"
    split = tmp_dir / "split.pdf"
    build_whole_floor_sheet(whole)
    build_split_sheet(split)

    rows = []
    for cap in CAPS_SQM:
        whole_faces = find_room_outlines(whole, 0, scale, max_sqm=cap)
        split_faces = find_room_outlines(split, 0, scale, max_sqm=cap)
        rows.append(
            {
                "上限sqm": cap,
                "仕切りなしの面": len(whole_faces),
                "仕切りなしの最大面積": (
                    round(max(f.area_sqm for f in whole_faces), 2)
                    if whole_faces
                    else None
                ),
                "仕切りありの面": len(split_faces),
                "仕切りありで名前の付いた面": sum(
                    1 for f in split_faces if f.name is not None
                ),
            }
        )
    first = rows[0]
    passed = bool(
        first["仕切りなしの面"] >= 1
        and any(r["仕切りなしの面"] == 0 for r in rows)
        and any(
            r["仕切りありの面"] >= 1
            for r in rows
            if r["仕切りなしの面"] == 0
        )
    )
    return {"通過": passed, "段ごと": rows}


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict | None:
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        notes = ceiling_notes(page)
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()
    if not notes:
        return None

    scale = _scale(pdf_path, page_index)
    decoy_points = scatter(notes, width, height, seed + page_index)
    rows = []
    for cap in CAPS_SQM:
        faces = find_room_outlines(pdf_path, page_index, scale, max_sqm=cap)
        polygons = [face.polygon_pt for face in faces]
        _, real_per_face = land(notes, polygons)
        _, decoy_per_face = land(decoy_points, polygons)
        # **結果を見てから足した診断。**線1〜線4 の判定には使っていない。
        # 行き先が 0 件になったとき、「注記を受ける面が落ちた」のか
        # 「面は残ったが 2 つ以上入っている」のかを分けるため。
        real_inside, _ = land(notes, polygons)
        rows.append(
            {
                "上限sqm": cap,
                "閉じた面": len(faces),
                "名前の付いた面": sum(1 for f in faces if f.name is not None),
                "行き先が1つ": settled(real_per_face),
                "囮の行き先が1つ": settled(decoy_per_face),
                "どれかの面に入った": real_inside,
                "注記が入った面": len(real_per_face),
            }
        )
    return {"ページ": page_index + 1, "注記": len(notes), "上限ごと": rows}


def measure(pdf_path: Path, seed: int, tmp_dir: Path) -> dict:
    definition = check_definition(tmp_dir)
    if not definition["通過"]:
        return {
            "線1_数え方の確かめ": definition,
            "実図面": "**線1 が通らなかったので測っていない**",
        }
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    kept = [
        row
        for row in (measure_page(pdf_path, i, seed) for i in range(pages))
        if row
    ]
    totals = []
    for position, cap in enumerate(CAPS_SQM):
        totals.append(
            {
                "上限sqm": cap,
                "閉じた面": sum(r["上限ごと"][position]["閉じた面"] for r in kept),
                "名前の付いた面": sum(
                    r["上限ごと"][position]["名前の付いた面"] for r in kept
                ),
                "行き先が1つ": sum(
                    r["上限ごと"][position]["行き先が1つ"] for r in kept
                ),
                "囮の行き先が1つ": sum(
                    r["上限ごと"][position]["囮の行き先が1つ"] for r in kept
                ),
            }
        )
    return {
        "線1_数え方の確かめ": definition,
        "注記の合計": sum(r["注記"] for r in kept),
        "上限ごとの合計": totals,
        "ページごと": kept,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--tmp", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    args.tmp.mkdir(parents=True, exist_ok=True)
    result = measure(args.pdf, args.seed, args.tmp)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
