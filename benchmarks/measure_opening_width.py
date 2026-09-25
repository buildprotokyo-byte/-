"""周11: **閉じた面が室にならないのは、開口の幅のせいか**を測る。

**この道具は数えるだけで、実装の既定値は変えない。**つなぐ幅は引数で渡す。

**主役は「嘘の壁」のほう**

幅を広げれば面は必ず増える。**増えた分が「室が割れた」のか「嘘の壁を引いた」のか
を分けないと、広げたほうが良いという結論に必ずなる。**

**嘘の壁 = 建具が無い所に引いた仮の辺。**仮の辺の中点から決めた距離のうちに
開き戸の円弧の中心(吊元)が無いものを数える。

**この数え方は嘘の壁を多めに数える。**引戸・折戸・建具無しの開口には円弧が
無いので、**そこに引いた正しい仮の辺も嘘に数えてしまう。**
多めに数えた数で「増えない」と言えるなら安全側である。
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
from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    MAX_GAP_MM,
    build_plan_graph,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import (  # noqa: E402
    DrawingScale,
    extract_scale,
    find_door_arcs,
)

#: 仮の辺の中点から、この距離のうちに吊元があれば「建具がある」とみなす(ミリ)。
DOOR_NEAR_MM = 600.0

#: 測るつなぐ幅(ミリ)。**1,200 が今の既定値。**
WIDTHS_MM = (1200.0, 1800.0, 2400.0, 3000.0)

#: 縮尺が読めなかったページで使う分母。
FALLBACK_DENOMINATOR = 50.0


def _scale(pdf_path: Path, page_index: int) -> DrawingScale:
    found = extract_scale(pdf_path, page_index)
    if found is not None:
        return found
    return DrawingScale(
        denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
    )


def open_gaps(
    pdf_path: Path, page_index: int, scale: DrawingScale, width_mm: float
) -> int:
    """**閉じられなかった切れ目**の本数(線1)。

    つなぐ幅を広げたときに新しく閉じられた辺の数を、その幅でのみ数える。
    ここでは「その幅より広い切れ目がいくつ残っているか」を、**もっと広い幅で
    閉じられた辺の数**として測る。
    """
    narrow = build_plan_graph(pdf_path, page_index, scale, max_gap_mm=width_mm)
    wide = build_plan_graph(
        pdf_path, page_index, scale, max_gap_mm=max(WIDTHS_MM) * 2.0
    )
    if narrow is None or wide is None:
        return 0
    return max(len(wide.virtual_edges) - len(narrow.virtual_edges), 0) // 2


def false_walls(
    pdf_path: Path,
    page_index: int,
    scale: DrawingScale,
    width_mm: float,
    near_mm: float = DOOR_NEAR_MM,
) -> dict:
    """**嘘の壁**(建具が無い所に引いた仮の辺)の本数。"""
    graph = build_plan_graph(pdf_path, page_index, scale, max_gap_mm=width_mm)
    if graph is None:
        return {
            "仮の辺": 0,
            "嘘の壁": 0,
            "建具の円弧": 0,
            "仮の辺の長さ": {"100mm未満": 0, "100-600mm": 0, "600mm以上": 0},
        }
    arcs = find_door_arcs(pdf_path, page_index, scale)
    hinges = [arc.center_pt for arc in arcs]
    near_pt = near_mm / graph.mm_per_pt

    seen: set[tuple[int, int]] = set()
    lies = 0
    total = 0
    # **結果を見てから足した診断。**線1〜線6 の判定には使っていない。
    # 仮の辺の長さを段に分けて数える。細い途切れの繕いと、開口をまたぐ線を
    # 分けるため(下の「嘘の壁」はこの 2 つを分けていない)。
    bands = {"100mm未満": 0, "100-600mm": 0, "600mm以上": 0}
    for u, v in graph.virtual_edges:
        key = (u, v) if u < v else (v, u)
        if key in seen:
            continue
        seen.add(key)
        total += 1
        ax, ay = graph.coords[u]
        bx, by = graph.coords[v]
        mid = ((ax + bx) / 2.0, (ay + by) / 2.0)
        length_mm = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 * graph.mm_per_pt
        if length_mm < 100.0:
            bands["100mm未満"] += 1
        elif length_mm < 600.0:
            bands["100-600mm"] += 1
        else:
            bands["600mm以上"] += 1
        close = any(
            (hx - mid[0]) ** 2 + (hy - mid[1]) ** 2 <= near_pt**2
            for hx, hy in hinges
        )
        if not close:
            lies += 1
    return {
        "仮の辺": total,
        "嘘の壁": lies,
        "建具の円弧": len(arcs),
        "仮の辺の長さ": bands,
    }


def measure_page(
    pdf_path: Path,
    page_index: int,
    seed: int,
    widths: tuple[float, ...] = WIDTHS_MM,
) -> dict | None:
    """天井高の注記があるページだけを測る。**値も室名も返さない。**"""
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
    for width_mm in widths:
        faces = find_room_outlines(pdf_path, page_index, scale, max_gap_mm=width_mm)
        polygons = [face.polygon_pt for face in faces]
        _, real_per_face = land(notes, polygons)
        _, decoy_per_face = land(decoy_points, polygons)
        row = {
            "つなぐ幅mm": width_mm,
            "閉じた面": len(faces),
            "行き先が1つ": settled(real_per_face),
            "囮の行き先が1つ": settled(decoy_per_face),
        }
        row.update(false_walls(pdf_path, page_index, scale, width_mm))
        rows.append(row)
    return {
        "ページ": page_index + 1,
        "注記": len(notes),
        "閉じられなかった切れ目": open_gaps(pdf_path, page_index, scale, MAX_GAP_MM),
        "幅ごと": rows,
    }


def measure(
    pdf_path: Path, seed: int, widths: tuple[float, ...] = WIDTHS_MM
) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    found = [measure_page(pdf_path, index, seed, widths) for index in range(pages)]
    kept = [row for row in found if row]
    totals = []
    for position, width_mm in enumerate(widths):
        totals.append(
            {
                "つなぐ幅mm": width_mm,
                "閉じた面": sum(r["幅ごと"][position]["閉じた面"] for r in kept),
                "行き先が1つ": sum(r["幅ごと"][position]["行き先が1つ"] for r in kept),
                "囮の行き先が1つ": sum(
                    r["幅ごと"][position]["囮の行き先が1つ"] for r in kept
                ),
                "仮の辺": sum(r["幅ごと"][position]["仮の辺"] for r in kept),
                "嘘の壁": sum(r["幅ごと"][position]["嘘の壁"] for r in kept),
            }
        )
    return {
        "線1_閉じられなかった切れ目": sum(r["閉じられなかった切れ目"] for r in kept),
        "注記の合計": sum(r["注記"] for r in kept),
        "幅ごとの合計": totals,
        "ページごと": kept,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--widths",
        type=float,
        nargs="+",
        default=list(WIDTHS_MM),
        help="つなぐ幅(ミリ)。**追記で狭い側を測るために足した**",
    )
    args = parser.parse_args()
    result = measure(args.pdf, args.seed, tuple(args.widths))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
