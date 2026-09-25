"""周15(空間): **室名が面の外周の近くにあるか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`find_room_outlines` の既定値は 1 つも動かしていない(K-29)。

**前の周と何が違うか**

周14 は室名を面の**内側**で探した(43 個中 4 個、囮 3 個)。
周15 は**外周を外へ広げた帯の中**で探す。**変えるのは探す場所ひとつだけ。**
面の取り方も囮の作り方も紙も種も同じ。

**なぜこれを測るのか**

周14 で「43 個は室ではない」とは書かなかった。**室名は輪郭の外に
置かれることがある**からである。ここが分かれ目になっている。

- **外にあるだけなら** … 壊れているのは「室名は輪郭の中にある」という
  実装の前提のほう(`RoomOutline.name` の取り方)。
- **外にも無いなら** … 面のある場所と室名のある場所が本当に別。

**帯の作り方(多角形を広げない)**

外へ広げた多角形を作る代わりに、**点の側から見る**。
「帯の中」とは、**その面の内側ではなく、輪郭までの距離が帯の幅以下で、
かつ他のどの面の内側でもない**点のことである。
多角形を広げると自分で交差して向きを間違えるので、そうしない。
**他の面の内側を落とすのは、隣の室の名前を自分の名前として
数えないため。**

基準は `docs/loop_round15_names_outside_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import _inside, build_plan_graph, find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from benchmarks.measure_height_destination import (  # noqa: E402
    FALLBACK_DENOMINATOR,
    ceiling_notes,
    scatter,
)
from benchmarks.measure_where_faces_are import (  # noqa: E402
    ROOM_WIDTH_MM,
    room_name_spans,
)

#: 外へ広げる帯の幅(実寸ミリ)。**測る前に決めた 3 段。**
BAND_MM = (500.0, 1000.0, 2000.0)

#: 囮との差が**これ以上**なら線1・線3 を通過とする。**測る前に決めた。**
#: 周14 の線2 が差 1 個で形の上は通過し、それを根拠にできなかったため。
MARGIN = 5

Point = tuple[float, float]
Polygon = tuple[Point, ...]


def point_to_segment(point: Point, start: Point, end: Point) -> float:
    """点から線分までの距離。**線ではなく線分**(端より外は端までの距離)。"""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0.0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / span
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def distance_to_boundary(point: Point, polygon: Polygon) -> float:
    """点から多角形の**輪郭**までの距離(内側にあっても正の値を返す)。"""
    return min(
        point_to_segment(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )


def band_faces(
    points: list[Point],
    polygons: list[Polygon],
    band_pt: float,
) -> dict[int, int]:
    """面ごとに、**外の帯の中に入った点の数**を返す。

    帯の中 = その面の内側ではない、輪郭までの距離が帯の幅以下、
    **他のどの面の内側でもない**。
    """
    out: dict[int, int] = {}
    for point in points:
        swallowed = any(_inside(point, list(polygon)) for polygon in polygons)
        if swallowed:
            continue  # どこかの面の内側。**周14 で数えた分。**
        for index, polygon in enumerate(polygons):
            if distance_to_boundary(point, polygon) <= band_pt:
                out[index] = out.get(index, 0) + 1
    return out


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
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
    graph = build_plan_graph(pdf_path, page_index, scale)
    spans = graph.spans if graph is not None else []
    mm_per_pt = graph.mm_per_pt if graph is not None else scale.mm_per_point

    wide = [face for face in faces if face.min_width_mm >= ROOM_WIDTH_MM]
    polygons = [face.polygon_pt for face in wide]

    named = room_name_spans(spans)
    name_points = [center for _, center, _ in named]
    decoy_names = scatter(name_points, width, height, seed + 1000 + page_index)
    decoy_notes = scatter(notes, width, height, seed + page_index)

    bands = {}
    for band_mm in BAND_MM:
        band_pt = band_mm / mm_per_pt
        real_names = band_faces(name_points, polygons, band_pt)
        fake_names = band_faces(decoy_names, polygons, band_pt)
        real_heights = band_faces(notes, polygons, band_pt)
        fake_heights = band_faces(decoy_notes, polygons, band_pt)
        bands[f"{band_mm:.0f}mm"] = {
            "線1_室名が帯にある面": len(real_names),
            "線1_囮": len(fake_names),
            "線2_室名がちょうど1つ": sum(1 for n in real_names.values() if n == 1),
            "線2_室名が2つ以上": sum(1 for n in real_names.values() if n >= 2),
            "線3_天井高が帯にある面": len(real_heights),
            "線3_囮": len(fake_heights),
        }

    return {
        "ページ": page_index + 1,
        "注記": len(notes),
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        "室名らしい文字": len(named),
        "帯": bands,
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    with_notes = [row for row in rows if row["注記"]]

    totals = {}
    for band_mm in BAND_MM:
        key = f"{band_mm:.0f}mm"
        totals[key] = {
            field: sum(row["帯"][key][field] for row in with_notes)
            for field in (
                "線1_室名が帯にある面",
                "線1_囮",
                "線2_室名がちょうど1つ",
                "線2_室名が2つ以上",
                "線3_天井高が帯にある面",
                "線3_囮",
            )
        }

    def passes(real_key: str, decoy_key: str) -> dict:
        """3 段のどこかで、本物が囮を MARGIN 個以上上回れば通過。"""
        gaps = {
            key: totals[key][real_key] - totals[key][decoy_key] for key in totals
        }
        return {"段ごとの差": gaps, "通過": any(gap >= MARGIN for gap in gaps.values())}

    narrow = totals[f"{BAND_MM[0]:.0f}mm"]
    return {
        "ページ": rows,
        "合計": totals,
        "線1_外の帯に室名はあるか": passes("線1_室名が帯にある面", "線1_囮"),
        "線2_いちばん狭い帯での混ざり方": {
            "ちょうど1つ": narrow["線2_室名がちょうど1つ"],
            "2つ以上": narrow["線2_室名が2つ以上"],
            "通過": narrow["線2_室名がちょうど1つ"] > narrow["線2_室名が2つ以上"],
        },
        "線3_天井高も外にあるのか": passes("線3_天井高が帯にある面", "線3_囮"),
    }


def build_check_sheet(path: Path) -> None:
    """**合成の紙**: 室名を囲いの**外**に刷ったもの。

    **外へ向かう向きを間違えていないかを、実図面に当てる前に確かめる紙**
    (周12 の教訓)。室名は囲いのすぐ外に置き、囲いの中には何も刷らない。
    """
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(200, 150, 400, 300))
    shape.finish(width=1)
    shape.commit()
    # 囲いのすぐ外(上)に室名、遠くにもう 1 つ
    page.insert_text((250, 140), "洋室", fontsize=9, fontname="japan")
    page.insert_text((60, 60), "便所", fontsize=9, fontname="japan")
    page.insert_text((250, 200), "CH=2400", fontsize=9)
    document.save(path)
    document.close()


def check_definition(tmp_dir: Path) -> dict:
    """**帯が外へ向いているかを合成の紙で確かめる。**

    すぐ外の室名は狭い帯で当たり、遠くの室名は当たらない。
    囲いの**中**に刷った天井高は、**帯には入らない**(内側だから)。
    """
    path = tmp_dir / "check.pdf"
    build_check_sheet(path)
    row = measure_page(path, 0, 20260925)
    return {
        "面": row[f"幅{ROOM_WIDTH_MM:.0f}mm以上の面"],
        "狭い帯_室名": row["帯"]["500mm"]["線1_室名が帯にある面"],
        "広い帯_室名": row["帯"]["2000mm"]["線1_室名が帯にある面"],
        "狭い帯_天井高": row["帯"]["500mm"]["線3_天井高が帯にある面"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="?", help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument(
        "--check",
        type=Path,
        default=None,
        help="合成の紙で帯の向きだけを確かめて終わる(作業用の置き場所を渡す)",
    )
    args = parser.parse_args()
    if args.check is not None:
        print(json.dumps(check_definition(args.check), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(measure(args.pdf, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
