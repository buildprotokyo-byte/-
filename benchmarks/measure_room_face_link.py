"""周17(空間): **仕上表の室名と、平面図の面が結び付くか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`find_room_outlines` も `read_finish_schedules` も既定値のまま(K-29)。

**前の周と何が違うか**

**探す語ひとつだけ。**周14・周15 は**こちらが用意した区分の語の表**に
当たる文字(5 ページで 91 個)を探した。ここでは**図面自身が仕上表に
並べた 10 個の室名**を探す。91 個には凡例・記事欄・注記など
**平面図の室の名札ではない文字**が混ざっているが、10 個は図面自身が
「この案件の室はこれだ」と並べたもので、**素性が違う。**

**なぜこれを測るのか**

周16 で分かったのは「足りないのは**室の一覧**ではなく**室と面の対応**」。
内壁面積は「その室の周長 × その室の天井高」なので、
**対応が 1 件も付かないかぎり 1 行も出せない。**

**囮は位置の囮に戻す**

周16 の囮A(列を取り違えた文字)は平面図に **0 か所**しか現れないので、
**位置を問うこの周では当たりようがない。当たりようのない囮は何も守らない**
(周10 の教訓)。だから周14・周15 と同じ「位置をばらばらに置き直す」囮を使う。

基準は `docs/loop_round17_room_face_link_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import _inside, find_room_outlines  # noqa: E402
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from benchmarks.measure_height_destination import (  # noqa: E402
    FALLBACK_DENOMINATOR,
    ceiling_notes,
    scatter,
)
from benchmarks.measure_names_outside import distance_to_boundary  # noqa: E402
from benchmarks.measure_schedule_source import collect  # noqa: E402
from benchmarks.measure_where_faces_are import ROOM_WIDTH_MM  # noqa: E402

#: 面の外へ広げる帯の幅(実寸ミリ)。**周15 のいちばん狭い帯と同じ。**
BAND_MM = 500.0

#: 線1・線2 の合格の差。**周15・周16 と同じ幅。測る前に決めた。**
MARGIN = 5

#: 線3 の合格。**10 個のうちこれだけ面が 1 つに決まれば通過。**
#: **実測で校正した値ではない**(基準にそう書いてある)。
LINE3_MIN = 3


def schedule_rooms(pdf_path: Path) -> list[str]:
    """仕上表に並んでいる室名(重複なし、出た順)。**語の表は通さない。**

    **周16 の診断の欄と同じ数え方。**区分の語で絞ると、図面が室として
    並べたものをこちらの語彙で落としてしまう。
    """
    found = collect(pdf_path)
    out: list[str] = []
    for name in found["室名"]:
        if name not in out:
            out.append(name)
    return out


def name_positions(
    page: pymupdf.Page, names: list[str]
) -> list[tuple[str, tuple[float, float]]]:
    """そのページで、室名が印字されている位置(文字の矩形の中心)。

    **一致はそのまま含むこと**(周16 と同じ決め方)。
    """
    out: list[tuple[str, tuple[float, float]]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                for name in names:
                    if name in text:
                        x0, y0, x1, y1 = span["bbox"]
                        out.append((name, ((x0 + x1) / 2.0, (y0 + y1) / 2.0)))
                        break
    return out


def reach(
    point: tuple[float, float],
    polygons: list[tuple[tuple[float, float], ...]],
    band_pt: float,
) -> list[int]:
    """その点が届く面(内側、または外へ帯の幅ぶん)。**複数返りうる。**"""
    inside = [
        index for index, polygon in enumerate(polygons) if _inside(point, list(polygon))
    ]
    if inside:
        return inside
    return [
        index
        for index, polygon in enumerate(polygons)
        if distance_to_boundary(point, polygon) <= band_pt
    ]


def measure_page(pdf_path: Path, page_index: int, names: list[str], seed: int) -> dict:
    """1 ページ分。**室名そのものは返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
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
    polygons = [face.polygon_pt for face in wide]
    mm_per_pt = scale.mm_per_point
    band_pt = BAND_MM / mm_per_pt

    points = [center for _, center in found]
    decoy_points = scatter(points, width, height, seed + page_index)

    real_reached = 0
    per_face: dict[int, set[str]] = {}
    per_room: dict[str, set[int]] = {}
    for name, point in found:
        hits = reach(point, polygons, band_pt)
        if hits:
            real_reached += 1
        for index in hits:
            per_face.setdefault(index, set()).add(name)
            per_room.setdefault(name, set()).add(index)

    # 囮は**位置だけ**を置き直す。**名前は本物のまま持たせる**ので、
    # 線2(面の側)も線3(室の側)も、本物とまったく同じ数え方で比べられる。
    decoy_reached = 0
    decoy_per_face: dict[int, set[str]] = {}
    decoy_per_room: dict[str, set[int]] = {}
    for (name, _), point in zip(found, decoy_points):
        hits = reach(point, polygons, band_pt)
        if hits:
            decoy_reached += 1
        for index in hits:
            decoy_per_face.setdefault(index, set()).add(name)
            decoy_per_room.setdefault(name, set()).add(index)

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        "室名が印字されていた数": len(found),
        "そのうち別々の室": len(per_room),
        "線1_面に届いた": real_reached,
        "線1_囮": decoy_reached,
        "線2_室名が1つに決まった面": sum(
            1 for names_here in per_face.values() if len(names_here) == 1
        ),
        "線2_囮": sum(
            1 for names_here in decoy_per_face.values() if len(names_here) == 1
        ),
        "線3_面が1つに決まった室": sum(
            1 for faces_here in per_room.values() if len(faces_here) == 1
        ),
        "線3_囮": sum(
            1 for faces_here in decoy_per_room.values() if len(faces_here) == 1
        ),
    }


def measure(pdf_path: Path, seed: int, with_text: bool) -> dict:
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

    line1_real, line1_decoy = total("線1_面に届いた"), total("線1_囮")
    line2_real, line2_decoy = total("線2_室名が1つに決まった面"), total("線2_囮")
    line3_real = total("線3_面が1つに決まった室")
    line3_decoy = total("線3_囮")

    result = {
        "ページ": plan,
        "仕上表の室": len(names),
        "線1_室名は面と重なる所にあるか": {
            "本物": line1_real,
            "囮": line1_decoy,
            "差": line1_real - line1_decoy,
            "通過": line1_real - line1_decoy >= MARGIN,
        },
        "線2_室名が1つに決まる面": {
            "本物": line2_real,
            "囮": line2_decoy,
            "差": line2_real - line2_decoy,
            "通過": line2_real - line2_decoy >= MARGIN,
        },
        "線3_面が1つに決まる室": {
            "本物": line3_real,
            "囮": line3_decoy,
            "差": line3_real - line3_decoy,
            "通過": line3_real >= LINE3_MIN and line3_real - line3_decoy >= LINE3_MIN,
        },
    }
    if with_text:
        result["室名(共有フォルダにのみ置く)"] = names
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="室名も返す。**出力は共有フォルダにしか置かないこと。**",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            measure(args.pdf, args.seed, args.with_text), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
