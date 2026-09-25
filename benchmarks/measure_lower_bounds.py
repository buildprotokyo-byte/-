"""周13: **面を室とみなす下限**(面積 0.5 ㎡・幅 400mm)が原因かを測る。

**この道具は数えるだけで、実装の既定値は変えない。**下限は引数で渡す。

**疑い**: 室の内側が家具や設備の線で細切れに割れていて、破片が幅の条件で
落ちている。**室は大きすぎて落ちているのではなく、細かく割れて落ちている。**

**拾いすぎる代償は、この実装が既に持っている判定で数える**
(`is_wall_cavity`。「狭いだけでは壁ではない。狭くて長いのが壁である」)。
**この数え方は細長い廊下を壁と呼ぶので、代償を多めに数える。**
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
    ROOM_MIN_SQM,
    ROOM_MIN_WIDTH_MM,
    find_room_outlines,
    is_wall_cavity,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale, extract_scale  # noqa: E402

#: 測る下限の段。**先頭が今の既定値。**(幅 mm, 面積 ㎡)
STEPS = (
    (400.0, 0.5),
    (300.0, 0.5),
    (200.0, 0.2),
    (100.0, 0.1),
)

FALLBACK_DENOMINATOR = 50.0


def _scale(pdf_path: Path, page_index: int) -> DrawingScale:
    found = extract_scale(pdf_path, page_index)
    if found is not None:
        return found
    return DrawingScale(
        denominator=FALLBACK_DENOMINATOR, source_text="縮尺が読めなかった"
    )


def build_plain_room(path: Path) -> None:
    """**合成の紙**: 室 1 つだけ。家具の線を置かない。"""
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 350, 350))
    shape.finish(width=1)
    shape.commit()
    document.save(path)
    document.close()


def build_room_with_fixture(path: Path) -> None:
    """**合成の紙**: 同じ室に、**家具の線を 1 本**壁から壁へ渡したもの。

    これが疑いそのもの。**線が一周を割る。**
    """
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 350, 350))
    # 壁から壁へ渡る線(家具の奥行き 600mm ぶんの位置)
    shape.draw_line(pymupdf.Point(50, 84), pymupdf.Point(350, 84))
    shape.finish(width=1)
    shape.commit()
    document.save(path)
    document.close()


def check_definition(tmp_dir: Path) -> dict:
    """線1: **合成の紙で、家具の線 1 本が輪を割り、破片が今の下限で落ちるか。**"""
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    plain = tmp_dir / "plain_room.pdf"
    fixture = tmp_dir / "room_with_fixture.pdf"
    build_plain_room(plain)
    build_room_with_fixture(fixture)

    plain_now = find_room_outlines(plain, 0, scale)
    fixture_now = find_room_outlines(fixture, 0, scale)
    fixture_loose = find_room_outlines(
        fixture, 0, scale, min_sqm=0.1, min_width_mm=100.0
    )
    # **線1 は「線 1 本で輪が割れること」までしか確かめない。**
    # 「400mm の条件が 400mm より狭い破片を落とすか」は条件の言い換えであって
    # 測定ではない(破片の幅はこちらが好きに決められる)。**追記で線1' に
    # 差し替えてある。**
    return {
        "通過": bool(len(plain_now) == 1 and len(fixture_now) > len(plain_now)),
        "家具なし": len(plain_now),
        "家具あり": len(fixture_now),
        "家具あり・下限をゆるめて": len(fixture_loose),
        "確かめたのは": "線 1 本で輪が割れることまで。破片が落ちるかは実図面で見る",
    }


def cavities(faces) -> int:
    """壁の中身と判定される面の数。**実装が持っている判定をそのまま使う。**"""
    return sum(
        1
        for face in faces
        if is_wall_cavity(
            (face.area_sqm, face.perimeter_mm, face.min_width_mm),
            ROOM_MIN_WIDTH_MM,
        )
    )


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict | None:
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
    for width_mm, min_sqm in STEPS:
        faces = find_room_outlines(
            pdf_path, page_index, scale, min_sqm=min_sqm, min_width_mm=width_mm
        )
        polygons = [face.polygon_pt for face in faces]
        _, real_per_face = land(notes, polygons)
        _, decoy_per_face = land(decoy_points, polygons)
        rows.append(
            {
                "幅mm": width_mm,
                "面積sqm": min_sqm,
                "閉じた面": len(faces),
                "壁の中身と判定": cavities(faces),
                "名前の付いた面": sum(1 for f in faces if f.name is not None),
                "行き先が1つ": settled(real_per_face),
                "囮の行き先が1つ": settled(decoy_per_face),
            }
        )
    return {"ページ": page_index + 1, "注記": len(notes), "段ごと": rows}


def crowding(pdf_path: Path, page_index: int, scale: DrawingScale) -> dict:
    """線1': **いまの下限のすぐ下に、面がどれだけ溜まっているか。**

    幅の条件だけを外し(面積の下限はそのまま)、**新しく現れる面の幅を
    段に分けて数える。****これは実図面でしか答えが出ない。**
    """
    now = find_room_outlines(pdf_path, page_index, scale)
    loose = find_room_outlines(pdf_path, page_index, scale, min_width_mm=1.0)
    known = {face.polygon_pt for face in now}
    bands = {"300-400mm": 0, "200-300mm": 0, "100-200mm": 0, "100mm未満": 0}
    for face in loose:
        if face.polygon_pt in known:
            continue
        width = face.min_width_mm
        if width >= 300.0:
            bands["300-400mm"] += 1
        elif width >= 200.0:
            bands["200-300mm"] += 1
        elif width >= 100.0:
            bands["100-200mm"] += 1
        else:
            bands["100mm未満"] += 1
    return {"今の下限での面": len(now), "新しく現れた面": len(loose) - len(now), "幅の段": bands}


def measure(pdf_path: Path, seed: int, tmp_dir: Path) -> dict:
    definition = check_definition(tmp_dir)
    if not definition["通過"]:
        return {
            "線1_合成での確かめ": definition,
            "実図面": "**合成で輪が割れなかったので測っていない**",
        }
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    kept = [
        row
        for row in (measure_page(pdf_path, i, seed) for i in range(pages))
        if row
    ]
    keys = ("閉じた面", "壁の中身と判定", "名前の付いた面", "行き先が1つ", "囮の行き先が1つ")
    totals = []
    for position, (width_mm, min_sqm) in enumerate(STEPS):
        row = {"幅mm": width_mm, "面積sqm": min_sqm}
        for key in keys:
            row[key] = sum(r["段ごと"][position][key] for r in kept)
        totals.append(row)
    crowded = [
        dict(ページ=r["ページ"], **crowding(pdf_path, r["ページ"] - 1, _scale(pdf_path, r["ページ"] - 1)))
        for r in kept
    ]
    return {
        "線1_合成での確かめ": definition,
        "線1ダッシュ_下限のすぐ下": crowded,
        "今の既定値": {"幅mm": ROOM_MIN_WIDTH_MM, "面積sqm": ROOM_MIN_SQM},
        "注記の合計": sum(r["注記"] for r in kept),
        "段ごとの合計": totals,
        "ページごと": kept,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--tmp", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    args.tmp.mkdir(parents=True, exist_ok=True)
    result = measure(args.pdf, args.seed, args.tmp)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
