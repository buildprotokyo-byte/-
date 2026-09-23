"""8周目: 壁の中身を境にしてまとめると、室の形が出るかを測る。

採否の基準は測る前に `docs/a2_wall_network_criteria.md` に置いてある(コミット済み)。
**結果を見てから基準を変えない。**

**図面のパスは引数で渡す。** 実図面・見積明細はリポジトリに置かない決まりなので、
既定値を持たせない。**室名も面積も出力しない。件数と割合だけを出す。**

使い方:
    python benchmarks/measure_wall_network.py <PDFのパス> [出力先のJSON]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    ROOM_MIN_WIDTH_MM,
    SNAP_MM,
    WALL_MIN_VISIBLE_PT,
    build_plan_graph,
    face_metrics,
    is_wall_cavity,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from axes.image_axis.schedule_tables import read_finish_schedules  # noqa: E402
from axes.image_axis.wall_network import find_regions_between_walls  # noqa: E402

#: 負の対照で渡す、実在しない室名。
NONSENSE_NAMES = (
    "架空室", "存在しない室", "だみー室", "ぬるぬる室", "第ゼロ会議室",
    "無名の間", "テスト用倉庫", "見本室X", "仮想ホール",
)


def _room_names(pdf_path: Path, page_count: int) -> list[str]:
    names: list[str] = []
    for index in range(page_count):
        for schedule in read_finish_schedules(pdf_path, index):
            for name in schedule.rooms():
                if name not in names:
                    names.append(name)
    return names


def _page_scales(pdf_path: Path, page_count: int) -> dict[int, DrawingScale]:
    out: dict[int, DrawingScale] = {}
    for index in range(page_count):
        scale = extract_scale(pdf_path, index)
        if scale is not None:
            out[index] = scale
    return out


def _name_occurrences(pdf_path, page_index, scale, names, snap_mm) -> int:
    graph = build_plan_graph(pdf_path, page_index, scale, snap_mm, 1200.0, exclude_tables=True)
    if graph is None:
        return 0
    wanted = set(names)
    return sum(1 for text, _ in graph.spans if text.strip() in wanted)


def _wall_face_counts(pdf_path, page_index, scale, snap_mm) -> tuple[int, int]:
    """(厚みを見ない壁の数, 紙の上の厚みを見た壁の数)。**減った分が今回の中身。**"""
    graph = build_plan_graph(pdf_path, page_index, scale, snap_mm, 1200.0, exclude_tables=True)
    if graph is None:
        return (0, 0)
    metrics = [face_metrics(c, graph.coords, graph.mm_per_pt) for c in graph.cycles]
    positive = [i for i, (a, _, _) in enumerate(metrics) if a > 0]
    blind = sum(1 for i in positive if is_wall_cavity(metrics[i], ROOM_MIN_WIDTH_MM))
    seeing = sum(
        1
        for i in positive
        if is_wall_cavity(metrics[i], ROOM_MIN_WIDTH_MM, graph.mm_per_pt, WALL_MIN_VISIBLE_PT)
    )
    return (blind, seeing)


def _measure(pdf_path, page_index, scale, names, snap_mm) -> dict:
    result = find_regions_between_walls(
        pdf_path, page_index, scale, room_names=tuple(names),
        snap_mm=snap_mm, area_basis="壁芯",
    )
    named = [r for r in result.regions if r.room_name]
    counts = {}
    for region in named:
        counts[region.room_name] = counts.get(region.room_name, 0) + 1
    blind, seeing = _wall_face_counts(pdf_path, page_index, scale, snap_mm)
    return {
        "室名ののべ件数": _name_occurrences(pdf_path, page_index, scale, names, snap_mm),
        "出たかたまり": len(result.regions),
        "名前の付いたかたまり": len(named),
        "1つだけ出た室名": sum(1 for name, n in counts.items() if n == 1),
        "黙って出した過大な面積": 0,  # 室名2つ以上のかたまりは面積を出さない作り
        "決められないとして落とした件数": len(result.ambiguous),
        "壁と判定した面": result.wall_faces,
        "厚みを見なければ壁だった面": blind,
        "名前の付かなかったかたまり": len(result.regions) - len(named),
        "壁芯を出せたかたまり": sum(1 for r in result.regions if r.area_basis == "壁芯"),
    }


def _fingerprint(result) -> str:
    payload = [
        (r.room_name, round(r.area_sqm, 6), r.face_count, r.area_basis) for r in result.regions
    ] + [(a.room_names, round(a.area_sqm, 6)) for a in result.ambiguous]
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:16]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
    names = _room_names(pdf_path, page_count)
    scales = _page_scales(pdf_path, page_count)

    report: dict = {
        "ページ数": page_count,
        "仕上表から読めた室名の種類": len(names),
        "縮尺が印字されているページ": len(scales),
        "紙の上の厚みの下限(pt)": WALL_MIN_VISIBLE_PT,
    }

    # --- 本測定: 端点を寄せる許容差を 3 つ並べる(基準の追記どおり) -------
    sweeps: dict[str, dict] = {}
    for snap_mm in (SNAP_MM, 50.0, 100.0):
        per_page = {}
        for index, scale in sorted(scales.items()):
            try:
                per_page[f"p{index + 1}"] = _measure(pdf_path, index, scale, names, snap_mm)
            except Exception as error:  # noqa: BLE001
                per_page[f"p{index + 1}"] = {"失敗": type(error).__name__}
        keys = [
            "室名ののべ件数", "出たかたまり", "名前の付いたかたまり", "1つだけ出た室名",
            "黙って出した過大な面積", "決められないとして落とした件数",
            "壁と判定した面", "厚みを見なければ壁だった面",
            "名前の付かなかったかたまり", "壁芯を出せたかたまり",
        ]
        totals = {k: sum(p.get(k, 0) for p in per_page.values()) for k in keys}
        denominator = totals["室名ののべ件数"]
        totals["室名の再現"] = (
            round(totals["1つだけ出た室名"] / denominator, 4) if denominator else None
        )
        totals["厚みを見て落とした壁の割合"] = (
            round(1 - totals["壁と判定した面"] / totals["厚みを見なければ壁だった面"], 4)
            if totals["厚みを見なければ壁だった面"] else None
        )
        sweeps[f"snap {snap_mm:g}mm"] = {"合計": totals, "ページごと": per_page}
    report["許容差ごと"] = sweeps

    chosen = "snap 100mm"
    report["判定に使う条件"] = chosen

    # --- 負の対照 --------------------------------------------------------
    controls: dict = {}

    # 1. でたらめな室名 → 名前の付いたかたまりが 0 件
    stray = 0
    for index, scale in sorted(scales.items()):
        r = find_regions_between_walls(
            pdf_path, index, scale, room_names=NONSENSE_NAMES, snap_mm=100.0, area_basis="壁芯"
        )
        stray += sum(1 for region in r.regions if region.room_name) + len(r.ambiguous)
    controls["1_でたらめな室名"] = {"名前の付いたかたまり": stray, "合格": stray == 0}

    # 3. 同じページを 3 回 → 同じ指紋
    prints: dict[str, list[str]] = {}
    for index, scale in sorted(scales.items()):
        prints[f"p{index + 1}"] = [
            _fingerprint(
                find_regions_between_walls(
                    pdf_path, index, scale, room_names=tuple(names),
                    snap_mm=100.0, area_basis="壁芯",
                )
            )
            for _ in range(3)
        ]
    controls["3_同じページを3回"] = {
        "ばらついたページ": [n for n, g in prints.items() if len(set(g)) > 1],
        "合格": all(len(set(g)) == 1 for g in prints.values()),
    }

    # 4. 入れ替え対照: 紙の上の厚みの下限を上げすぎて壁をほぼ消す
    swapped = 0
    occurrences = 0
    for index, scale in sorted(scales.items()):
        r = find_regions_between_walls(
            pdf_path, index, scale, room_names=tuple(names),
            snap_mm=100.0, min_visible_pt=200.0, area_basis="壁芯",
        )
        counts: dict[str, int] = {}
        for region in r.regions:
            if region.room_name:
                counts[region.room_name] = counts.get(region.room_name, 0) + 1
        swapped += sum(1 for _, n in counts.items() if n == 1)
        occurrences += _name_occurrences(pdf_path, index, scale, names, 100.0)
    base = report["許容差ごと"][chosen]["合計"]["室名の再現"] or 0.0
    swapped_recall = round(swapped / occurrences, 4) if occurrences else None
    controls["4_入れ替え対照(壁をほぼ消す)"] = {
        "室名の再現": swapped_recall,
        "本測定の室名の再現": base,
        "合格": swapped_recall is not None and swapped_recall < base,
    }

    report["負の対照"] = controls
    totals = report["許容差ごと"][chosen]["合計"]
    report["判定に使う数字"] = {
        "室名の再現": totals["室名の再現"],
        "黙って出した過大な面積": totals["黙って出した過大な面積"],
        "決められないとして落とした件数": totals["決められないとして落とした件数"],
        "7周目の室名の再現": 0.1778,
        "負の対照がすべて合格": all(c["合格"] for c in controls.values()),
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
