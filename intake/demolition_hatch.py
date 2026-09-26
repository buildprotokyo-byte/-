"""既存撤去図の**青い網**を面として測る(K-42)。

なぜ測るのか
------------
K-41 周 9・周 11(`docs/k41_loop_round9_demolition_hatch_criteria.md`、
`docs/k41_loop_round11_demolition_split_criteria.md`)で、既存撤去図の青い斜めの
線の網を面にすると、床組の撤去・天井組の撤去の正解の数量と ±5% 以内で合った。
黒い網の囮は外れた。**名前では網と囮を分けられず、分けたのは面積だけ**だった。

手順(周 9 の試作と同じ値。測りながら動かさない)
-----------------------------------------------
1. 線の色が青(許容つき)で、傾きが 30〜60 度(と、その鏡)の線分を集める。
2. 1pt の太さで塗る。
3. 網の目を埋めるため、40pt の四角で閉じる(膨らませてから縮める)。
4. 穴を埋める。
5. つながった面ごとに面積を測り、**そのページの縮尺**で㎡にする。

縮尺は入口(`intake/drawing_intake.py`)がページごとに決めたもの(印字・記入された
寸法・人の基準点を突き合わせた結果)を受け取る。**縮尺が無ければ面積を出さない。
推測しない。**

1 つの面から 2 行
-----------------
凡例 m は青の交差した網を「床組・天井組の撤去範囲」と名乗る。周 9 で 1 行に
まとめたら、正解が床組と天井組の別の 2 行なので「まとめ行」になった。だから
**面ごとに「床組 撤去」と「天井組 撤去」の 2 行**を、同じ面積で出す。
凡例の文が図面のどこにも見当たらなければ、行は出すが根拠に
「凡例の記載が見当たりません」と書く。**意味を推し量らない。**

この道がしないこと
------------------
- 何も確定させない。面積は候補。
- 少し外れたもの(青に近い色、傾きが 20〜30 度・60〜70 度、網の目 1 つより小さい面)は
  捨てずに「候補(近いが外れ)」に理由つきで残す。見積の行にはしない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pymupdf

from intake.plan_colour_notes import BLUE, classify_colour, colour_hex, nfkc

#: 塗る細かさ(1pt あたりの画素)。
PIXELS_PER_POINT = 2.0
#: 線を塗る太さ(pt)。
STROKE_PT = 1.0
#: 網の目を埋める四角の一辺(pt)。周 9 で網の目が約 30pt だったので 40pt。
CLOSE_PT = 40.0
#: 網とみなす傾き(度、水平から)。**45 度ちょうどだけを見ない。**
SLOPE_MIN_DEG = 30.0
SLOPE_MAX_DEG = 60.0
#: 近いが外れとみなす傾きの幅(許容の外側に、この度数まで)。
SLOPE_NEAR_DEG = 10.0
#: 面とみなす最小の広さ(紙の上の pt²)。**網の目 1 つ(40pt 角)ぶん。**
#: 縮尺に依らない紙の上の値にしてある(縮尺が読めなくても同じ線で分ける)。
MIN_REGION_PT2 = CLOSE_PT * CLOSE_PT

WORK_FLOOR = "床組 撤去"
WORK_CEILING = "天井組 撤去"
LEGEND_BASIS = "凡例は青い交差した網を「床組・天井組の撤去範囲」と名乗っている"
LEGEND_MISSING = "凡例の記載が見当たりません"


@dataclass(frozen=True)
class HatchRegion:
    """青い網の面 1 つ。**面積は候補で、確定ではない。**"""

    page: int
    area_pt2: float
    area_sqm: float | None
    """縮尺が無ければ ``None``。**0 にしない。**"""
    bbox_pt: tuple[float, float, float, float]
    places: tuple[str, ...]


@dataclass
class HatchResult:
    page: int
    regions: list[HatchRegion] = field(default_factory=list)
    near_misses: list[dict[str, Any]] = field(default_factory=list)
    segments_used: int = 0
    mm_per_point: float | None = None
    scale_text: str | None = None
    legend_found: bool = False
    legend_text: str | None = None


def _angle_deg(dx: float, dy: float) -> float:
    """水平からの傾き(0〜90 度)。右上がりも右下がりも同じに扱う(鏡)。"""
    return math.degrees(math.atan2(abs(dy), abs(dx)))


def _slope_status(angle: float) -> str:
    if SLOPE_MIN_DEG <= angle <= SLOPE_MAX_DEG:
        return "ok"
    if SLOPE_MIN_DEG - SLOPE_NEAR_DEG <= angle <= SLOPE_MAX_DEG + SLOPE_NEAR_DEG:
        return "near"
    return "out"


def find_legend_text(pdf_path: str | Path) -> str | None:
    """図面のどこかに、床組と天井組の撤去範囲を名乗る文の行があれば、その文字。"""
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", ()):
                    text = nfkc("".join(span["text"] for span in line["spans"]))
                    if "床組" in text and "天井組" in text and "撤去" in text:
                        return text
    return None


def measure_demolition_hatch(
    pdf_path: str | Path,
    page_number: int,
    *,
    mm_per_point: float | None,
    scale_text: str | None = None,
    room_labels: Mapping[str, Sequence[tuple[float, float]]] | None = None,
    legend_text: str | None = None,
) -> HatchResult:
    """1 ページ(1 始まり)の青い網を面にして測る。

    ``mm_per_point`` は紙の 1pt が実寸で何 mm か(入口が決めたそのページの縮尺)。
    **``None`` なら面積は出さない**(面そのものは残す)。
    ``legend_text`` は凡例の文(`find_legend_text`)。無ければ ``None``。
    """
    import cv2

    result = HatchResult(
        page=page_number,
        mm_per_point=mm_per_point,
        scale_text=scale_text,
        legend_found=legend_text is not None,
        legend_text=legend_text,
    )
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            raise IndexError(f"ページ {page_number} は存在しません")
        page = doc.load_page(page_number - 1)
        width = int(math.ceil(page.rect.width * PIXELS_PER_POINT)) + 1
        height = int(math.ceil(page.rect.height * PIXELS_PER_POINT)) + 1
        canvas = np.zeros((height, width), dtype=np.uint8)
        thickness = max(1, int(round(STROKE_PT * PIXELS_PER_POINT)))
        for drawing in page.get_drawings():
            rgb = drawing.get("color")
            colour, near_colour = classify_colour(rgb)
            if colour != BLUE and near_colour != BLUE:
                continue
            for item in drawing["items"]:
                if item[0] != "l":
                    continue
                a, b = item[1], item[2]
                dx, dy = b.x - a.x, b.y - a.y
                if math.hypot(dx, dy) < 0.5:
                    continue
                angle = _angle_deg(dx, dy)
                status = _slope_status(angle)
                if status == "out":
                    continue
                where = [round(a.x, 1), round(a.y, 1), round(b.x, 1), round(b.y, 1)]
                if colour != BLUE:
                    if status == "ok":
                        result.near_misses.append(
                            {
                                "ページ": page_number,
                                "線分_pt": where,
                                "色": colour_hex(rgb),
                                "傾き_度": round(angle, 1),
                                "理由": "色が青に近いが許容の外",
                            }
                        )
                    continue
                if status == "near":
                    result.near_misses.append(
                        {
                            "ページ": page_number,
                            "線分_pt": where,
                            "色": colour_hex(rgb),
                            "傾き_度": round(angle, 1),
                            "理由": f"傾きが許容({SLOPE_MIN_DEG:g}〜{SLOPE_MAX_DEG:g} 度)の外",
                        }
                    )
                    continue
                result.segments_used += 1
                cv2.line(
                    canvas,
                    (int(round(a.x * PIXELS_PER_POINT)), int(round(a.y * PIXELS_PER_POINT))),
                    (int(round(b.x * PIXELS_PER_POINT)), int(round(b.y * PIXELS_PER_POINT))),
                    255,
                    thickness,
                )

    if result.segments_used == 0:
        return result

    size = int(round(CLOSE_PT * PIXELS_PER_POINT))
    closed = cv2.morphologyEx(canvas, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(closed)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((filled > 0).astype(np.uint8), 8)
    px2 = PIXELS_PER_POINT * PIXELS_PER_POINT
    for index in range(1, count):
        x, y, w, h, pixels = (int(v) for v in stats[index])
        area_pt2 = pixels / px2
        bbox = (
            round(x / PIXELS_PER_POINT, 1),
            round(y / PIXELS_PER_POINT, 1),
            round((x + w) / PIXELS_PER_POINT, 1),
            round((y + h) / PIXELS_PER_POINT, 1),
        )
        if area_pt2 < MIN_REGION_PT2:
            result.near_misses.append(
                {
                    "ページ": page_number,
                    "外接_pt": list(bbox),
                    "面積_pt2": round(area_pt2, 1),
                    "理由": f"網の目 1 つ({CLOSE_PT:g}pt 角 = {MIN_REGION_PT2:g}pt²)より小さい面",
                }
            )
            continue
        places = []
        for name, positions in (room_labels or {}).items():
            for px, py in positions:
                ix, iy = int(px * PIXELS_PER_POINT), int(py * PIXELS_PER_POINT)
                if 0 <= iy < labels.shape[0] and 0 <= ix < labels.shape[1] and labels[iy, ix] == index:
                    places.append(name)
                    break
        area_sqm = None
        if mm_per_point is not None and mm_per_point > 0:
            area_sqm = round(area_pt2 * (mm_per_point / 1000.0) ** 2, 2)
        result.regions.append(
            HatchRegion(
                page=page_number,
                area_pt2=round(area_pt2, 1),
                area_sqm=area_sqm,
                bbox_pt=bbox,
                places=tuple(places),
            )
        )
    result.regions.sort(key=lambda r: -r.area_pt2)
    return result
