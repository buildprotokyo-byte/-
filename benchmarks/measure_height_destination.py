"""周5(空間): **天井高の注記に、行き先があるか**を数える。

**この道具は数えるだけで、本番の経路には繋がっていない。**
天井高を人の入力以外から受け取る口は作っていない(K-29)。

**なぜこれを測るのか**

内壁面積は「その室の周長 × **その室の**天井高」である。
天井高の数字が紙にいくつあっても、**どの室のものか決まらなければ 1 件も出せない。**
だから数えるのは「数字がいくつあるか」ではなく「**行き先が 1 つに決まる数字が
いくつあるか**」である。

**囮**

注記の位置を、同じページの中でばらばらに動かす(種は引数)。
閉じた面はページのある割合を覆っているので、**でたらめな点でもその割合ぶんは
面の内側に落ちる。囮の件数が、その「ただ当たる分」である。**

**あとから足した欄が 1 つある**

``注記を受けた面の面積の順位`` は、**結果を見てから足した診断の欄**である。
線1・線2・線3 の判定には使っていない。足した理由は、注記がどのページでも
1〜3 個の面にしか入らなかったので、**その面が室なのか紙全体なのかを
分ける**必要が出たため。**先に決めた線は動かしていない。**

**この道具が原理的に落とすもの**

- スキャンされたページ。線が図形データとして入っていないので面は常に 0 件。
- 開口が広くて閉じられない室。`find_room_outlines` が閉じずに漏らす。
- **面が室とは限らない。**実図面では取れている面の大半が室ではないことが
  分かっている。だからこの道具が言えるのは「**面**に行き先が決まるか」までで、
  「**室**に決まるか」ではない。報告でも書き分けること。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    _inside,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402

#: 天井高の注記の書き方。実図面では 3 段の枠ではなく ``CH=`` で書かれている
#: (共有フォルダの `reports/K-22_天井高は図面にある.md`)。
CEILING_NOTE = re.compile(r"CH\s*[=＝]\s*([0-9０-９,，]{3,6})")

#: 断面の図とみなす図面名称の語。**図面リストの名称をそのまま見る。**
SECTION_WORDS = ("断面", "矩計", "展開", "立面")

#: 縮尺が読めなかったページで使う分母。**読めたページでは使わない。**
#: 分母は面積の窓にしか効かず、面の内側かどうかの判定には効かない。
FALLBACK_DENOMINATOR = 50.0


def ceiling_notes(page: pymupdf.Page) -> list[tuple[float, float]]:
    """``CH=`` の注記の位置(文字の矩形の中心)を返す。**値は返さない。**"""
    found: list[tuple[float, float]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if not CEILING_NOTE.search(text):
                continue
            x0, y0, x1, y1 = line["bbox"]
            found.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return found


def scatter(
    points: list[tuple[float, float]],
    width: float,
    height: float,
    seed: int,
) -> list[tuple[float, float]]:
    """囮: 同じ数の点を、紙の中にばらばらに置き直す。

    **数は変えない。**変えるのは位置だけで、そこがこの囮の言いたいところ。
    """
    rng = random.Random(seed)
    return [
        (rng.uniform(0.0, width), rng.uniform(0.0, height)) for _ in points
    ]


def land(
    points: list[tuple[float, float]],
    polygons: list[tuple[tuple[float, float], ...]],
) -> tuple[int, dict[int, int]]:
    """点が面の内側に入った件数と、面ごとに入った点の数を返す。"""
    inside = 0
    per_face: dict[int, int] = {}
    for point in points:
        hit = None
        for index, polygon in enumerate(polygons):
            if _inside(point, list(polygon)):
                hit = index
                break
        if hit is None:
            continue
        inside += 1
        per_face[hit] = per_face.get(hit, 0) + 1
    return inside, per_face


def settled(per_face: dict[int, int]) -> int:
    """**ちょうど 1 つ**だけ注記が入った面に収まる注記の数。

    2 つ以上入った面は行き先が決まらない(面が室より大きいか、面が室でない)。
    """
    return sum(1 for count in per_face.values() if count == 1)


def measure_page(
    pdf_path: Path,
    page_index: int,
    seed: int,
) -> dict:
    """1 ページ分。**天井高の値も室名も返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        notes = ceiling_notes(page)
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()
    if not notes:
        return {"ページ": page_index + 1, "注記": 0}

    scale = extract_scale(pdf_path, page_index)
    if scale is None:
        scale = DrawingScale(
            denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
        )
    faces = find_room_outlines(pdf_path, page_index, scale)
    polygons = [face.polygon_pt for face in faces]

    real_inside, real_per_face = land(notes, polygons)
    decoy_points = scatter(notes, width, height, seed + page_index)
    decoy_inside, decoy_per_face = land(decoy_points, polygons)

    order = sorted(
        range(len(faces)), key=lambda index: faces[index].area_sqm, reverse=True
    )
    rank = {face: position + 1 for position, face in enumerate(order)}

    return {
        "ページ": page_index + 1,
        "注記": len(notes),
        "閉じた面": len(faces),
        "注記を受けた面の面積の順位": sorted(
            rank[face] for face in real_per_face
        ),
        "縮尺が読めた": scale.source_text != "縮尺が読めなかった",
        "面の内側に入った": real_inside,
        "囮が面の内側に入った": decoy_inside,
        "行き先が1つに決まった": settled(real_per_face),
        "囮で行き先が1つに決まった": settled(decoy_per_face),
        "注記が入った面": len(real_per_face),
    }


def section_drawings(pdf_path: Path) -> dict:
    """線1: 1 ページ目の図面リストから、断面の図を数える。

    **名称そのものは返さない**(図面の中身なので共有フォルダに置く)。
    """
    document = pymupdf.open(pdf_path)
    try:
        text = document[0].get_text()
    finally:
        document.close()
    hits = {word: text.count(word) for word in SECTION_WORDS}
    return {"図面リストに出た回数": hits, "合計": sum(hits.values())}


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    with_notes = [row for row in rows if row["注記"]]
    total = sum(row["注記"] for row in with_notes)
    real = sum(row["面の内側に入った"] for row in with_notes)
    decoy = sum(row["囮が面の内側に入った"] for row in with_notes)
    one = sum(row["行き先が1つに決まった"] for row in with_notes)
    decoy_one = sum(row["囮で行き先が1つに決まった"] for row in with_notes)
    return {
        "線1_断面の図": section_drawings(pdf_path),
        "注記のあるページ": len(with_notes),
        "注記の合計": total,
        "線2_面の内側": {"本物": real, "囮": decoy, "線": "本物 >= 囮 x 2"},
        "線3_行き先が1つ": {"本物": one, "囮": decoy_one, "線": "24 件以上"},
        "ページごと": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF のパス(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = measure(args.pdf, args.seed)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
