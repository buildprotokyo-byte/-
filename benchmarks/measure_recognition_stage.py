"""**1 段目(認識)**を測る。図面の上にあるものを、どれだけ捉えられているか。

なぜこれが要るのか
------------------
おーちゃんの指摘。**「認識をしていなければ、読むも読まないもない。」**
これまでの「不明」「0 件」「落ちた」は、**認識で落ちたのか、読むで落ちたのかが
混ざっていた** → `docs/k21_reading_stages.md`。

**正解は人が作らない**
----------------------
ベクターの PDF は、**その紙に何がどこにあるかの台帳を自分で持っている。**
``page.get_drawings()`` は線・曲線・矩形の 1 本 1 本を、``page.get_text()`` は
語の 1 つ 1 つを、位置つきで返す。**これを分母にする。**
分子は「**いまの経路が触れた命令**」である。

**触れた = 認識した、ではない。**罫線を升目に取り込んだからといって、その線を
「表の枠」として認識したとは限らない。**この道具が出すのは上限である。**
**スキャンのページでは使えない**(描画命令が無いので常に 0 件になる)。

実行::

    python -m benchmarks.measure_recognition_stage --pdf <図面.pdf> --page 24
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import pymupdf

from axes.image_axis.pdf_room_outlines import build_plan_graph, find_room_outlines
from axes.image_axis.pdf_tables import find_tables
from axes.image_axis.pdf_vector_symbols import extract_scale, find_door_arcs

#: 表題欄はこれより左。事務所名・個人名・登録番号が入るので**範囲に入れない**。
TITLE_BLOCK_X = 75.0

#: 選んだ面の外接矩形に足す余白(pt)。基準に先に書いた値。
PADDING = 20.0

#: 対照 D1 で範囲をずらす量(pt)。基準に先に書いた値。
SHIFT = 50.0

#: 面の線分として集められる描画命令の種類(`pdf_room_outlines._segments` と同じ)。
SEGMENT_KINDS = ("l", "re", "qu")


def frame_centre(page: pymupdf.Page, title_block_x: float = TITLE_BLOCK_X) -> tuple[float, float]:
    """**表題欄を外した図枠**の中心。"""
    rect = page.rect
    return ((rect.x0 + title_block_x + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)


def _bbox(face: Any) -> pymupdf.Rect:
    xs = [point[0] for point in face.polygon_pt]
    ys = [point[1] for point in face.polygon_pt]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def choose_face(faces: list[Any], centre: tuple[float, float]) -> Any | None:
    """**図枠の中心にいちばん近い面**を選ぶ。

    ただし **ほかの候補の面を 1 つでも内側に含む面は選ばない**(基準の追記 1 の続き)。
    外形や通り芯の枠は、ほかの室を含むので室ではない。
    **含まない面が 1 つも無ければ、そのまま全部から選ぶ**(下りる先を黙って失わない)。
    """
    if not faces:
        return None
    boxes = [_bbox(face) for face in faces]
    standalone = [
        face
        for index, face in enumerate(faces)
        if not any(
            other != index and boxes[index].contains(boxes[other]) for other in range(len(faces))
        )
    ]
    pool = standalone or faces
    return min(
        pool,
        key=lambda face: (
            ((_bbox(face).x0 + _bbox(face).x1) / 2.0 - centre[0]) ** 2
            + ((_bbox(face).y0 + _bbox(face).y1) / 2.0 - centre[1]) ** 2
        ),
    )


def range_rect(face: Any, padding: float = PADDING) -> pymupdf.Rect:
    """選んだ面の外接矩形に余白を足した範囲。"""
    box = _bbox(face)
    return pymupdf.Rect(box.x0 - padding, box.y0 - padding, box.x1 + padding, box.y1 + padding)


def item_rects(page: pymupdf.Page) -> list[tuple[tuple[int, int], str, pymupdf.Rect]]:
    """**描画命令を 1 つずつ**、((パス番号, 命令番号), 種類, 外接矩形) で返す。"""
    out: list[tuple[tuple[int, int], str, pymupdf.Rect]] = []
    for path_index, drawing in enumerate(page.get_drawings()):
        for item_index, item in enumerate(drawing["items"]):
            kind = item[0]
            points: list[tuple[float, float]] = []
            for value in item[1:]:
                if isinstance(value, pymupdf.Point):
                    points.append((value.x, value.y))
                elif isinstance(value, pymupdf.Rect):
                    points.extend([(value.x0, value.y0), (value.x1, value.y1)])
                elif isinstance(value, pymupdf.Quad):
                    points.extend(
                        [(value.ul.x, value.ul.y), (value.ur.x, value.ur.y),
                         (value.lr.x, value.lr.y), (value.ll.x, value.ll.y)]
                    )
            if not points:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            out.append(
                ((path_index, item_index), kind, pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)))
            )
    return out


def untouched(
    items: list[tuple[tuple[int, int], str, pymupdf.Rect]],
    touched: dict[str, set[tuple[int, int]]],
) -> list[tuple[tuple[int, int], str]]:
    """**どの経路も触れなかった命令**を、番号と種類で返す。"""
    seen: set[tuple[int, int]] = set()
    for keys in touched.values():
        seen |= keys
    return [(key, kind) for key, kind, _ in items if key not in seen]


def _inside(rect: pymupdf.Rect, window: pymupdf.Rect) -> bool:
    return bool(rect.intersects(window))


def measure(
    pdf_path: Path | str, page_number: int, window: pymupdf.Rect, scale: Any
) -> dict[str, Any]:
    """範囲の中の描画命令と文字を数え、**経路ごとに触れた件数**を返す。"""
    page_index = page_number - 1
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        items = [(key, kind, rect) for key, kind, rect in item_rects(page) if _inside(rect, window)]
        words = [word for word in page.get_text("words") if _inside(pymupdf.Rect(*word[:4]), window)]

        touched: dict[str, set[tuple[int, int]]] = {}

        arcs = find_door_arcs(pdf_path, page_index, scale)
        arc_rects = [pymupdf.Rect(*arc.rect_pt) for arc in arcs if _inside(pymupdf.Rect(*arc.rect_pt), window)]
        touched["ベクターの図形(円弧)"] = {
            key for key, _, rect in items if any(box.contains(rect) for box in arc_rects)
        }

        # **表の外形ではなく升目で数える。**外形で数えると、表が 1 つ見つかっただけで
        # 範囲の中の命令が全部「触れた」ことになり、**0 件という嘘の満点**が出る
        # (最初に実装したとき実際にそうなった)。
        cell_rects: list[pymupdf.Rect] = []
        tables = 0
        for region in find_tables(pdf_path, page_index):
            if not _inside(pymupdf.Rect(*region.rect_pt), window):
                continue
            tables += 1
            for row in region.rows:
                for cell in row:
                    box = pymupdf.Rect(*cell.rect_pt)
                    if _inside(box, window):
                        cell_rects.append(box)
        # **升目の中に入っていることは、その命令を認識したことではない。**
        # 経路が出している要素は「升目」であって、升目の中に描かれている線や曲線ではない。
        # 数えはするが、**「触れた」には入れない**(入れると、まちがって見つかった表 1 つで
        # 範囲の中の命令が全部「認識できた」ことになる)。
        inside_cells = {
            key for key, _, rect in items if any(box.contains(rect) for box in cell_rects)
        }

        touched["面の線分"] = {key for key, kind, _ in items if kind in SEGMENT_KINDS}

        left = untouched(items, touched)

    kinds: dict[str, int] = {}
    for _, kind in left:
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "範囲": [round(value, 1) for value in (window.x0, window.y0, window.x1, window.y1)],
        "分母": {"描画命令": len(items), "文字(語)": len(words)},
        "触れた": {name: len(keys) for name, keys in touched.items()},
        "どの経路も触れなかった描画命令": len(left),
        "その内訳": kinds,
        "文字の読み取りが拾った語": len(words),
        "建具の円弧": len(arc_rects),
        "罫線の表": tables,
        "表の升目": len(cell_rects),
        "升目の中に入っていた命令(**認識には数えない**)": len(inside_cells),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--shift", type=float, default=SHIFT, help="対照 D1 でずらす量(pt)")
    args = parser.parse_args(argv)

    page_index = args.page - 1
    scale = extract_scale(args.pdf, page_index)
    if scale is None:
        print("縮尺が読めませんでした。**縮尺が読めないことも 1 段目の結果である。**")
        return 1

    outlines = find_room_outlines(args.pdf, page_index, scale)
    step = "find_room_outlines"
    if not outlines:
        graph = build_plan_graph(args.pdf, page_index, scale, 20.0, 300.0)
        outlines = []
        step = "面の数え上げ(下りた)"
    with pymupdf.open(args.pdf) as doc:
        centre = frame_centre(doc[page_index])
    face = choose_face(outlines, centre)
    if face is None:
        window = pymupdf.Rect(centre[0] - 100, centre[1] - 100, centre[0] + 100, centre[1] + 100)
        step = "図枠の中心の 200pt 角(下りた)"
    else:
        window = range_rect(face)

    result = {
        "ページ": args.page,
        "縮尺": f"1/{scale.denominator:g}",
        "閉じた領域": len(outlines),
        "範囲の決め方": step,
        "選んだ面": None
        if face is None
        else {"面積㎡": round(face.area_sqm, 2), "最小の幅mm": round(face.min_width_mm)},
        "本番": measure(args.pdf, args.page, window, scale),
    }
    shifted = pymupdf.Rect(
        window.x0 + args.shift, window.y0 + args.shift, window.x1 + args.shift, window.y1 + args.shift
    )
    result["対照 D1 ずらした範囲"] = measure(args.pdf, args.page, shifted, scale)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
