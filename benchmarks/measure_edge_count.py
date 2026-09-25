"""周19(空間): **辺の数で室かどうかを分けられるか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`find_room_outlines` に辺の数の条件は入れていない。**絞るのはここだけ**(K-29)。

**前の周と何が違うか**

**絞る目盛りひとつだけ。**周11 は**つなぐ幅**、周12 は**面積**、周13 は**幅**で
絞った。**3 つとも面の「大きさ」を見る目盛り**だった。
ここで使うのは**辺の数**で、**大きさではなく形**を見る。

**なぜこれを測るのか**

周18 で、室名が集まっている面の辺の数が **164・1,144・2,093 本**と出た。
**室の輪郭は普通 4〜12 本で足りる。桁が 2 つ違う。**

**辺が少ない面が室とは限らない**

図面枠・凡例の四角も辺は少ない。罫線の表の升目は実装が落としているが、
それ以外は落ちていない。**だから線1 が通っても「室が見つかった」とは
書かない。**効いたかどうかは線2・線3(手がかりが入るか)で見る。

基準は `docs/loop_round19_edge_count_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from benchmarks.measure_height_destination import (  # noqa: E402
    FALLBACK_DENOMINATOR,
    ceiling_notes,
    scatter,
)
from benchmarks.measure_room_face_link import (  # noqa: E402
    BAND_MM,
    name_positions,
    reach,
    schedule_rooms,
)
from benchmarks.measure_where_faces_are import ROOM_WIDTH_MM  # noqa: E402

#: 室らしい形とみなす辺の数の上限。**測る前に決めた。**
#: 四角い室なら 4 本、出窓・床の間・建具の引き込み・柱形があっても 20 本で足りる。
#: **実測で校正した値ではない。**
ROOM_MAX_EDGES = 20

#: 線1 の合格(個数)。平面図が 5 ページあるので**1 ページ 1 個**を下限にした。
#: **実測で校正した値ではない。**
LINE1_MIN = 5

#: 線2・線3 の合格の差。絞ったあとの面は少ないので小さい標本に合わせた。
#: **実測で校正した値ではない。**
MARGIN = 3

#: 帯。**判定には使わない。何が起きているかを見るためだけ。**
BANDS = ((4, 12), (13, 20), (21, 50), (51, 200), (201, None))


def band_of(edges: int) -> str:
    for low, high in BANDS:
        if high is None:
            if edges >= low:
                return f"{low}本以上"
        elif low <= edges <= high:
            return f"{low}〜{high}本"
    return f"{BANDS[0][0]}本未満"


def measure_page(pdf_path: Path, page_index: int, names: list[str], seed: int) -> dict:
    """1 ページ分。**天井高の値も室名も返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        notes = ceiling_notes(page)
        if not notes:
            return {"ページ": page_index + 1, "平面図とみなす": False}
        found = name_positions(page, names)
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
    band_pt = BAND_MM / scale.mm_per_point

    bands: dict[str, int] = {}
    for face in wide:
        key = band_of(len(face.polygon_pt))
        bands[key] = bands.get(key, 0) + 1

    simple = [face for face in wide if len(face.polygon_pt) <= ROOM_MAX_EDGES]
    polygons = [face.polygon_pt for face in simple]

    def faces_hit(points: list[tuple[float, float]]) -> int:
        hit: set[int] = set()
        for point in points:
            hit.update(reach(point, polygons, band_pt))
        return len(hit)

    def one_name(points: list[tuple[str, tuple[float, float]]]) -> int:
        per_face: dict[int, set[str]] = {}
        for name, point in points:
            for index in reach(point, polygons, band_pt):
                per_face.setdefault(index, set()).add(name)
        return sum(1 for got in per_face.values() if len(got) == 1)

    decoy_notes = scatter(notes, width, height, seed + page_index)
    name_points = [point for _, point in found]
    decoy_names = scatter(name_points, width, height, seed + 1000 + page_index)
    decoy_found = [(name, point) for (name, _), point in zip(found, decoy_names)]

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        "辺の数の帯": bands,
        f"線1_辺が{ROOM_MAX_EDGES}本以下の面": len(simple),
        "線2_天井高が入った面": faces_hit(notes),
        "線2_囮": faces_hit(decoy_notes),
        "線3_室名が1つに決まった面": one_name(found),
        "線3_囮": one_name(decoy_found),
    }


def measure(pdf_path: Path, seed: int) -> dict:
    names = schedule_rooms(pdf_path)
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, names, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    def total(key: str) -> int:
        return sum(row[key] for row in plan)

    bands: dict[str, int] = {}
    for row in plan:
        for key, count in row["辺の数の帯"].items():
            bands[key] = bands.get(key, 0) + count

    simple = total(f"線1_辺が{ROOM_MAX_EDGES}本以下の面")
    line2, line2_decoy = total("線2_天井高が入った面"), total("線2_囮")
    line3, line3_decoy = total("線3_室名が1つに決まった面"), total("線3_囮")

    return {
        "ページ": plan,
        "合計": {
            f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": total(f"幅{ROOM_WIDTH_MM:.0f}mm以上の面"),
            "辺の数の帯": bands,
        },
        f"線1_辺が{ROOM_MAX_EDGES}本以下の面は在るか": {
            "本物": simple,
            "通過": simple >= LINE1_MIN,
        },
        "線2_その面に天井高は入るか": {
            "本物": line2,
            "囮": line2_decoy,
            "差": line2 - line2_decoy,
            "通過": line2 - line2_decoy >= MARGIN,
        },
        "線3_その面に室名が1つに決まるか": {
            "本物": line3,
            "囮": line3_decoy,
            "差": line3 - line3_decoy,
            "通過": line3 - line3_decoy >= MARGIN,
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
