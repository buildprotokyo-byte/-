"""周20(空間): **辺が少ないきれいな 17 個の面が何なのか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`find_room_outlines` に辺の数の条件は入れていない(K-29)。

**前の周と何が違うか**

**見る相手ひとつだけ。**周14 は**43 個全部**を相手にした。
ここでは**辺 20 本以下の 17 個だけ**を相手にする。

**なぜこれを測るのか**

周19 で「17 個はちゃんと閉じた形をしているのに、天井高の注記が
1 件も入らない」と分かった。**では何なのか。**
ここを読めれば、直す先が「**面を作り直す**」なのか
「**面は合っていて、天井高の側が別の場所にある**」なのかが分かれる。

**囮があるのは線3 だけ**

線1(面積)と線2(入れ子)は**図形をそのまま測る**ので囮を置かない。
**だから線1・線2 が通っても「効いた」とは書かない。**
位置を当てているかを見ているのは**線3 だけ**である。

基準は `docs/loop_round20_simple_faces_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import _inside, find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from benchmarks.measure_edge_count import ROOM_MAX_EDGES  # noqa: E402
from benchmarks.measure_height_destination import (  # noqa: E402
    FALLBACK_DENOMINATOR,
    ceiling_notes,
    scatter,
)
from benchmarks.measure_names_outside import distance_to_boundary  # noqa: E402
from benchmarks.measure_where_faces_are import ROOM_WIDTH_MM  # noqa: E402

#: 線1: 面積の中央値がこれ未満なら「水回り・収納の大きさ」。
#: 押入 1.6 ㎡、便所 1.6 ㎡、浴室 3.3 ㎡、4.5 畳 7.4 ㎡ の境目に置いた。
#: **実測で校正した値ではない。**
LINE1_SQM = 3.0

#: 線2: 化け物の面とみなす辺の数(周19 の帯の下から 3 番目の境目)。
BLOB_EDGES = 51

#: 線2: 17 個のうちこの割合以上が入れ子なら「入れ子になっている」。
LINE2_SHARE = 12 / 17

#: 線3: 本物の中央距離が囮のこの倍率以下なら「近い」。
LINE3_RATIO = 0.8


def nested(small: tuple[tuple[float, float], ...], big: tuple[tuple[float, float], ...]) -> bool:
    """小さい輪郭が、大きい輪郭の内側に**まるごと**入っているか。"""
    return all(_inside(point, list(big)) for point in small)


def nearest(point: tuple[float, float], polygons: list) -> float | None:
    """その点から、いちばん近い輪郭までの距離(ポイント)。無ければ None。"""
    if not polygons:
        return None
    return min(distance_to_boundary(point, polygon) for polygon in polygons)


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
    """1 ページ分。**天井高の値は返さない。件数と大きさだけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        notes = ceiling_notes(page)
        if not notes:
            return {"ページ": page_index + 1, "平面図とみなす": False}
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()

    scale = extract_scale(pdf_path, page_index)
    if scale is None:
        scale = DrawingScale(
            denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
        )
    faces = find_room_outlines(pdf_path, page_index, scale)
    wide = [face for face in faces if face.min_width_mm >= ROOM_WIDTH_MM]
    simple = [face for face in wide if len(face.polygon_pt) <= ROOM_MAX_EDGES]
    blobs = [face for face in wide if len(face.polygon_pt) >= BLOB_EDGES]
    mm_per_pt = scale.mm_per_point

    inside_blob = sum(
        1
        for face in simple
        if any(nested(face.polygon_pt, blob.polygon_pt) for blob in blobs)
    )

    polygons = [face.polygon_pt for face in simple]
    real = [
        distance * mm_per_pt
        for distance in (nearest(point, polygons) for point in notes)
        if distance is not None
    ]
    decoy_points = scatter(notes, width, height, seed + page_index)
    fake = [
        distance * mm_per_pt
        for distance in (nearest(point, polygons) for point in decoy_points)
        if distance is not None
    ]

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        f"辺が{ROOM_MAX_EDGES}本以下の面": len(simple),
        f"辺が{BLOB_EDGES}本以上の面": len(blobs),
        "線2_化け物の内側に入れ子": inside_blob,
        "面積㎡": [round(face.area_sqm, 2) for face in simple],
        "天井高からの距離mm": [round(value) for value in real],
        "囮の距離mm": [round(value) for value in fake],
    }


def spread(values: list[float]) -> dict:
    """**中央値は 17 個しかないと 1 個で動くので、最小・最大・四分位も出す。**

    **判定に使うのは中央値だけ**(基準の落とし穴 2)。
    """
    if not values:
        return {"件数": 0}
    ordered = sorted(values)
    quantiles = (
        statistics.quantiles(ordered, n=4) if len(ordered) >= 4 else [None, None, None]
    )
    return {
        "件数": len(ordered),
        "最小": round(ordered[0], 2),
        "四分の一": None if quantiles[0] is None else round(quantiles[0], 2),
        "中央値": round(statistics.median(ordered), 2),
        "四分の三": None if quantiles[2] is None else round(quantiles[2], 2),
        "最大": round(ordered[-1], 2),
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    areas = [value for row in plan for value in row["面積㎡"]]
    real = [value for row in plan for value in row["天井高からの距離mm"]]
    fake = [value for row in plan for value in row["囮の距離mm"]]
    simple = sum(row[f"辺が{ROOM_MAX_EDGES}本以下の面"] for row in plan)
    inside_blob = sum(row["線2_化け物の内側に入れ子"] for row in plan)

    real_median = statistics.median(real) if real else None
    fake_median = statistics.median(fake) if fake else None
    area_median = statistics.median(areas) if areas else None

    return {
        "ページ": plan,
        f"辺が{ROOM_MAX_EDGES}本以下の面": simple,
        "線1_面積": {
            "中央値㎡": None if area_median is None else round(area_median, 2),
            "ばらつき": spread(areas),
            "見立てどおり(水回り・収納の大きさ)": area_median is not None
            and area_median < LINE1_SQM,
            "囮": "置いていない(図形をそのまま測る線)",
        },
        "線2_化け物の内側に入れ子か": {
            "入れ子": inside_blob,
            "全部": simple,
            "割合": None if not simple else round(inside_blob / simple, 3),
            "入れ子になっている": bool(simple) and inside_blob / simple >= LINE2_SHARE,
            "囮": "置いていない(図形の包含関係そのもの)",
        },
        "線3_天井高は17個の近くにあるか": {
            "本物の中央距離mm": None if real_median is None else round(real_median),
            "囮の中央距離mm": None if fake_median is None else round(fake_median),
            "本物のばらつき": spread(real),
            "囮のばらつき": spread(fake),
            "通過": real_median is not None
            and fake_median is not None
            and real_median <= fake_median * LINE3_RATIO,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()
    print(json.dumps(measure(args.pdf, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
