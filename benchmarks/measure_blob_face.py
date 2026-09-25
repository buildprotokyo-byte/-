"""周18(空間): **室名が集まる面と、天井高が集まる面が同じ面か**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
既定値は 1 つも動かしていない(K-29)。

**前の周と何が違うか**

**数える向きひとつだけ。**周17 は「**室名 → 面**」を数えた。
ここでは「**面 → 2 種類の手がかりの重なり**」を数える。

**なぜこれを測るのか**

周5・周14 で「天井高はどのページでも**面積で 2 番目の面**に落ちる」と分かり、
周17 で「**別々の室の名前が同じ面に集まっている**」と分かった。
**この 2 つが同じ面のことを言っているなら、直す先は 1 つに絞れる。
別の面なら、詰まりは 2 か所ある。**

**ページが 5 枚しかない**

どの線も小さい標本である。**通過しても「効いた」とは書かない。**
書けるのは「**この 5 ページではこう見えた**」まで。

基準は `docs/loop_round18_blob_face_criteria.md`(測る前にコミット済み)。
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

#: 線1 の合格の差(ページ数)。**ページが 5 枚しかないので 2。**
#: **実測で校正した値ではない**(基準にそう書いてある)。
LINE1_MARGIN = 2

#: 線2: 「複数の室を抱えている」とみなす、別々の室名の数。
LINE2_ROOMS = 3

#: 線2 の合格のページ数。
LINE2_PAGES = 3

#: 線3: 仮の辺がこの割合以上なら「繕いで繋ぎ止められている」とみなす。
LINE3_SHARE = 0.1


def top_face(counts: dict[int, int]) -> int | None:
    """いちばん多く集まった面。**同数で 1 つに決まらなければ None。**

    **そのページは数えない**(本物も囮も同じ扱い)。基準の落とし穴 2。
    """
    if not counts:
        return None
    best = max(counts.values())
    winners = [index for index, count in counts.items() if count == best]
    return winners[0] if len(winners) == 1 else None


def gather(
    points: list[tuple[str, tuple[float, float]]],
    polygons: list[tuple[tuple[float, float], ...]],
    band_pt: float,
) -> tuple[dict[int, int], dict[int, set[str]]]:
    """面ごとに、届いた点の数と、届いた**別々の名前**を返す。"""
    counts: dict[int, int] = {}
    names: dict[int, set[str]] = {}
    for name, point in points:
        for index in reach(point, polygons, band_pt):
            counts[index] = counts.get(index, 0) + 1
            names.setdefault(index, set()).add(name)
    return counts, names


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
    polygons = [face.polygon_pt for face in wide]
    band_pt = BAND_MM / scale.mm_per_point

    name_counts, name_sets = gather(found, polygons, band_pt)
    note_counts, _ = gather([("", point) for point in notes], polygons, band_pt)

    decoy_points = scatter([point for _, point in found], width, height, seed + page_index)
    decoy_found = [(name, point) for (name, _), point in zip(found, decoy_points)]
    decoy_counts, decoy_sets = gather(decoy_found, polygons, band_pt)

    name_top = top_face(name_counts)
    note_top = top_face(note_counts)
    decoy_top = top_face(decoy_counts)

    row: dict = {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        f"幅{ROOM_WIDTH_MM:.0f}mm以上の面": len(wide),
        "室名が印字されていた数": len(found),
        "天井高の注記": len(notes),
        "室名の面が1つに決まった": name_top is not None,
        "天井高の面が1つに決まった": note_top is not None,
        "線1_同じ面": name_top is not None and note_top is not None and name_top == note_top,
        "線1_囮": decoy_top is not None and note_top is not None and decoy_top == note_top,
        "線2_別々の室名": len(name_sets.get(name_top, ())) if name_top is not None else 0,
        "線2_囮": len(decoy_sets.get(decoy_top, ())) if decoy_top is not None else 0,
    }

    if name_top is not None:
        face = wide[name_top]
        order = sorted(
            range(len(faces)), key=lambda index: faces[index].area_sqm, reverse=True
        )
        rank = {id(faces[index]): position + 1 for position, index in enumerate(order)}
        share = face.virtual_edges / len(face.polygon_pt) if face.polygon_pt else 0.0
        row["室名が集まる面"] = {
            "面積の順位": rank.get(id(face)),
            "面積㎡": round(face.area_sqm, 2),
            "幅mm": round(face.min_width_mm, 1),
            "辺の数": len(face.polygon_pt),
            "仮の辺": face.virtual_edges,
            "線3_仮の辺の割合": round(share, 3),
            "線3_繕いで繋ぎ止められている": share >= LINE3_SHARE,
        }
    return row


def measure(pdf_path: Path, seed: int) -> dict:
    names = schedule_rooms(pdf_path)
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, names, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    same = sum(1 for row in plan if row["線1_同じ面"])
    same_decoy = sum(1 for row in plan if row["線1_囮"])
    many = sum(1 for row in plan if row["線2_別々の室名"] >= LINE2_ROOMS)
    many_decoy = sum(1 for row in plan if row["線2_囮"] >= LINE2_ROOMS)
    patched = [
        row["ページ"]
        for row in plan
        if row.get("室名が集まる面", {}).get("線3_繕いで繋ぎ止められている")
    ]

    return {
        "ページ": plan,
        "仕上表の室": len(names),
        "線1_室名の面と天井高の面は同じか": {
            "本物": same,
            "囮": same_decoy,
            "差": same - same_decoy,
            "通過": same - same_decoy >= LINE1_MARGIN,
        },
        "線2_その面は複数の室を抱えているか": {
            "本物": many,
            "囮": many_decoy,
            "通過": many >= LINE2_PAGES and many - many_decoy >= 1,
        },
        "線3_繕いで繋ぎ止められていないか": {
            "繋ぎ止められていたページ": patched,
            "通過": not patched,
        },
        "参考_面が1つに決まらなかったページ": [
            row["ページ"]
            for row in plan
            if not row["室名の面が1つに決まった"] or not row["天井高の面が1つに決まった"]
        ],
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
