"""7周目: 仕上表の室名を種にして、割れた区画が室にまとまるかを測る。

採否の基準は測る前に `docs/a2_room_selection_criteria.md` に置いてある
(コミット済み)。**結果を見てから基準を変えない。**

**図面のパスは引数で渡す。** 実図面・見積明細はリポジトリに置かない決まりなので、
既定値を持たせない。**室名も面積も出力しない。件数と割合だけを出す。**

使い方:
    python benchmarks/measure_room_selection.py <PDFのパス> [出力先のJSON]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_room_outlines import (  # noqa: E402
    ROOM_MIN_SQM,
    build_plan_graph,
    face_metrics,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402
from axes.image_axis.room_regions import find_room_regions  # noqa: E402
from axes.image_axis.schedule_tables import read_finish_schedules  # noqa: E402

#: 負の対照 1 で渡す、実在しない室名。**図面には無い。**
NONSENSE_NAMES = (
    "架空室", "存在しない室", "だみー室", "ぬるぬる室", "第ゼロ会議室",
    "無名の間", "テスト用倉庫", "見本室X", "仮想ホール",
)


def _room_names(pdf_path: Path, page_count: int) -> list[str]:
    """全ページの内装仕上表から室名を集める。**名前そのものは返すが出力しない。**"""
    names: list[str] = []
    for index in range(page_count):
        for schedule in read_finish_schedules(pdf_path, index):
            for name in schedule.rooms():
                if name not in names:
                    names.append(name)
    return names


def _page_scales(pdf_path: Path, page_count: int) -> dict[int, DrawingScale]:
    """ページごとに**印字されている**縮尺。読めないページは入れない。

    6 周目に測ったとおり、**縮尺は 1 冊の中でページごとに違う。**
    実寸の窓を持つ手法を、縮尺を確かめずに動かさない。
    """
    out: dict[int, DrawingScale] = {}
    for index in range(page_count):
        scale = extract_scale(pdf_path, index)
        if scale is not None:
            out[index] = scale
    return out


def _name_occurrences(
    pdf_path: Path, page_index: int, scale: DrawingScale, names: list[str]
) -> int:
    """そのページの本文に出てくる室名の**のべ件数**(表の中は除く)。分母になる。"""
    graph = build_plan_graph(pdf_path, page_index, scale, 5.0, 1200.0, exclude_tables=True)
    if graph is None:
        return 0
    wanted = set(names)
    return sum(1 for text, _ in graph.spans if text.strip() in wanted)


def _measure_page(
    pdf_path: Path, page_index: int, scale: DrawingScale, names: list[str]
) -> dict:
    result = find_room_regions(
        pdf_path, page_index, scale, room_names=tuple(names), area_basis="壁芯"
    )
    sized = [r for r in result.regions if ROOM_MIN_SQM <= r.area_sqm]
    return {
        "室名ののべ件数": _name_occurrences(pdf_path, page_index, scale, names),
        "出た領域": len(result.regions),
        "室の大きさの領域": len(sized),
        "まとまった室名の種類": len({r.room_name for r in result.regions}),
        "1つだけ出た室名": len(
            {
                name
                for name in {r.room_name for r in result.regions}
                if sum(1 for r in result.regions if r.room_name == name) == 1
            }
        ),
        "取り違え(室名が2つ以上入った領域)": len(result.ambiguous),
        "まとめた区画が2つ以上の領域": sum(1 for r in result.regions if r.face_count > 1),
        "壁芯を出せた領域": sum(1 for r in result.regions if r.area_basis == "壁芯"),
        "仮に閉じた辺を含む領域": sum(1 for r in result.regions if r.virtual_edges > 0),
        "出さなかった理由": len(result.notes),
    }


def _fingerprint(result) -> str:
    """結果の指紋。**3 回が同じかどうかを、中身を出さずに比べるため。**"""
    payload = [
        (r.room_name, round(r.area_sqm, 6), r.face_count, r.area_basis)
        for r in result.regions
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
    }

    per_page: dict[str, dict] = {}
    for index, scale in sorted(scales.items()):
        try:
            per_page[f"p{index + 1}"] = _measure_page(pdf_path, index, scale, names)
        except Exception as error:  # noqa: BLE001
            per_page[f"p{index + 1}"] = {"失敗": type(error).__name__}
    report["ページごと"] = per_page

    keys = [
        "室名ののべ件数", "出た領域", "室の大きさの領域", "まとまった室名の種類",
        "1つだけ出た室名", "取り違え(室名が2つ以上入った領域)",
        "まとめた区画が2つ以上の領域", "壁芯を出せた領域", "仮に閉じた辺を含む領域",
    ]
    totals = {key: sum(page.get(key, 0) for page in per_page.values()) for key in keys}
    denominator = totals["室名ののべ件数"]
    totals["室名の再現(1つだけ出た室名 ÷ のべ件数)"] = (
        round(totals["1つだけ出た室名"] / denominator, 4) if denominator else None
    )
    report["合計"] = totals

    # --- 負の対照 ---------------------------------------------------------
    controls: dict = {}

    # 1. でたらめな室名 → 期待: 0 件
    nonsense = 0
    for index, scale in sorted(scales.items()):
        result = find_room_regions(
            pdf_path, index, scale, room_names=NONSENSE_NAMES, area_basis="壁芯"
        )
        nonsense += len(result.regions) + len(result.ambiguous)
    controls["1_でたらめな室名"] = {"出た領域": nonsense, "合格": nonsense == 0}

    # 2. 図面でないページ(縮尺が印字されていないページ)→ 期待: 0 件
    non_plan = [i for i in range(page_count) if i not in scales]
    stray = 0
    for index in non_plan:
        result = find_room_regions(
            pdf_path, index, DrawingScale(denominator=50.0, source_text="1/50"),
            room_names=tuple(names), area_basis="壁芯",
        )
        stray += len(result.regions)
    controls["2_図面でないページ"] = {
        "ページ数": len(non_plan), "出た領域": stray, "合格": stray == 0,
    }

    # 3. 同じページを 3 回 → 期待: 同じ指紋
    prints: dict[str, list[str]] = {}
    for index, scale in sorted(scales.items()):
        prints[f"p{index + 1}"] = [
            _fingerprint(
                find_room_regions(
                    pdf_path, index, scale, room_names=tuple(names), area_basis="壁芯"
                )
            )
            for _ in range(3)
        ]
    controls["3_同じページを3回"] = {
        "ばらついたページ": [name for name, got in prints.items() if len(set(got)) > 1],
        "合格": all(len(set(got)) == 1 for got in prints.values()),
    }

    # 4. 室名を 1 つだけ渡す → 期待: その室の面積が、全部渡したときと同じ
    mismatch = 0
    compared = 0
    for index, scale in sorted(scales.items()):
        everything = find_room_regions(
            pdf_path, index, scale, room_names=tuple(names), area_basis="壁芯"
        )
        for region in everything.regions:
            alone = find_room_regions(
                pdf_path, index, scale, room_names=(region.room_name,), area_basis="壁芯"
            )
            same = [r for r in alone.regions if r.room_name == region.room_name]
            compared += 1
            if len(same) != 1 or abs(same[0].area_sqm - region.area_sqm) > 1e-6:
                mismatch += 1
    controls["4_室名を1つだけ渡す"] = {
        "比べた領域": compared, "食い違った領域": mismatch, "合格": mismatch == 0,
    }

    report["負の対照"] = controls
    report["判定に使う数字"] = {
        "室名の再現": totals["室名の再現(1つだけ出た室名 ÷ のべ件数)"],
        "取り違え": totals["取り違え(室名が2つ以上入った領域)"],
        "負の対照がすべて合格": all(c["合格"] for c in controls.values()),
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
