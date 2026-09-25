"""周24(空間): **空いた区間に、読めなかった数字は入っているのか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`pdf_dimensions` の定数は 1 つも動かしていない(K-29)。

周23 で、**横の寸法が連ならない理由は、同じ寸法線に乗った読みどうしが
隣り合っていないこと**だと分かった(縦は 79 組すべて隣り合い、
横は 31 組のうち **26 組で間が空いている**)。
**そこで周23 はこう書いて止めた。**

> **「読めなかった数字を読めば連なる」とは書かない。**
> **間が空いた 26 組のあいだに、落とした数字が実際にあるかを数えていない。**

**ここを測る。**`read_dimensions()` は読めなかった数字を
`DimensionPage.skipped` に位置つきで残しているので、**位置で突き合わせられる。**

基準は `docs/loop_round24_gaps_hold_numbers_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis.pdf_dimensions import read_dimensions  # noqa: E402
from benchmarks.measure_height_destination import ceiling_notes  # noqa: E402
from benchmarks.measure_horizontal_chain import same_line  # noqa: E402

#: 空きとみなす最小の隙間(ポイント)。**周23 の連なりと同じ 5pt。**
GAP_MIN_PT = 5.0

#: 空きの帯の高さ(寸法線から上下にこれだけ)。**近似である。**
#: `pdf_dimensions` の `TEXT_GAP_HEIGHT_FACTOR`(1.5)と
#: `TEXT_GAP_CONSTANT_PT`(2.0)から、字の高さ 7pt のときおよそ 12pt。
#: **この案件の字の高さを測って決めた値ではない。**
BAND_PT = 12.0

Reading = tuple[str, tuple[float, float], tuple[float, float]]
Gap = tuple[float, float, float, float]


def gaps(readings: list[Reading]) -> list[Gap]:
    """同じ寸法線に乗り、**間が空いている**横の読み 2 件の、その隙間の矩形。"""
    out: list[Gap] = []
    for index, first in enumerate(readings):
        for other_index in range(index + 1, len(readings)):
            second = readings[other_index]
            if not same_line(first, second):
                continue
            left = sorted([first[1][0], first[2][0]])
            right = sorted([second[1][0], second[2][0]])
            if right[0] - left[1] >= GAP_MIN_PT:
                x0, x1 = left[1], right[0]
            elif left[0] - right[1] >= GAP_MIN_PT:
                x0, x1 = right[1], left[0]
            else:
                continue
            level = (first[1][1] + first[2][1] + second[1][1] + second[2][1]) / 4.0
            out.append((x0, level - BAND_PT, x1, level + BAND_PT))
    return out


def inside(point: tuple[float, float], box: Gap) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def count_in_gaps(
    boxes: list[Gap], points: list[tuple[float, float]]
) -> dict[str, int]:
    """空きごとに、落とした数字がいくつ入るか。"""
    per_gap = [sum(1 for point in points if inside(point, box)) for box in boxes]
    return {
        "空き": len(boxes),
        "落とし物が1個以上入った空き": sum(1 for value in per_gap if value >= 1),
        "ちょうど1個だけ入った空き": sum(1 for value in per_gap if value == 1),
    }


def scatter_points(
    points: list[tuple[float, float]], width: float, height: float, seed: int
) -> list[tuple[float, float]]:
    """囮: **個数はそのままに、紙の中でばらばらに置き直す。**

    **0 を返すしかない囮ではない。**空きは紙の上の帯なので、
    でたらめに置いた点でも入る。
    """
    rng = random.Random(seed)
    return [(rng.uniform(0.0, width), rng.uniform(0.0, height)) for _ in points]


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
    """1 ページ分。**数字そのものは返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
            return {"ページ": page_index + 1, "平面図とみなす": False}
        width, height = page.rect.width, page.rect.height
    finally:
        document.close()

    got = read_dimensions(pdf_path, page_index)
    yoko: list[Reading] = [
        (reading.orientation, reading.start_pt, reading.end_pt)
        for reading in got.readings
        if reading.orientation == "横"
    ]
    boxes = gaps(yoko)
    points = [
        ((item.rect_pt[0] + item.rect_pt[2]) / 2.0, (item.rect_pt[1] + item.rect_pt[3]) / 2.0)
        for item in got.skipped
    ]
    reasons = [item.reason for item in got.skipped]

    # **(空き, 落とし物)の組の数。**空きは重なりうるので、
    # **同じ数字が 2 つの空きに入れば 2 回数える。**
    in_gaps: dict[str, int] = {}
    for box in boxes:
        for point, reason in zip(points, reasons):
            if inside(point, box):
                in_gaps[reason] = in_gaps.get(reason, 0) + 1

    # **重複を除いた数。**どれか 1 つでも空きに入った落とし物を 1 回だけ数える。
    distinct: dict[str, int] = {}
    for point, reason in zip(points, reasons):
        if any(inside(point, box) for box in boxes):
            distinct[reason] = distinct.get(reason, 0) + 1

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        "落とした数字": len(points),
        "線1_本物": count_in_gaps(boxes, points),
        "線1_囮": count_in_gaps(boxes, scatter_points(points, width, height, seed + page_index)),
        "線3_入っていた落とし物の理由": in_gaps,
        "線3_重複を除いた落とし物の理由": distinct,
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    def total(key: str, label: str) -> int:
        return sum(row[key][label] for row in plan)

    boxes = total("線1_本物", "空き")
    hit = total("線1_本物", "落とし物が1個以上入った空き")
    decoy = total("線1_囮", "落とし物が1個以上入った空き")
    exactly_one = total("線1_本物", "ちょうど1個だけ入った空き")
    reasons: dict[str, int] = {}
    distinct: dict[str, int] = {}
    for row in plan:
        for reason, count in row["線3_入っていた落とし物の理由"].items():
            reasons[reason] = reasons.get(reason, 0) + count
        for reason, count in row["線3_重複を除いた落とし物の理由"].items():
            distinct[reason] = distinct.get(reason, 0) + count

    return {
        "ページ": plan,
        "落とした数字": sum(row["落とした数字"] for row in plan),
        "線1_空きに読めなかった数字は入っているか": {
            "空き": boxes,
            "本物": hit,
            "囮": decoy,
            "合格": "空きの過半",
            "通過": hit * 2 > boxes and hit > decoy,
        },
        "線2_空き1つにつき何個入っているか": {
            "落とし物が入った空き": hit,
            "ちょうど1個": exactly_one,
            "合格": "入った空きの過半",
            "通過": exactly_one * 2 > hit if hit else False,
            "囮": "置いていない(入った中の内訳であって位置を当てる線ではない)",
        },
        "線3_入っていた落とし物の理由": {
            "内訳_組の数(空きは重なるので同じ数字を2度数えうる)": dict(
                sorted(reasons.items(), key=lambda item: -item[1])
            ),
            "内訳_重複を除いた数字の数": dict(
                sorted(distinct.items(), key=lambda item: -item[1])
            ),
            "先に書いた予想": "「対応する寸法線が見つからない」が最も多い",
            "判定": "置いていない(記述であって通過・不通過の線ではない)",
        },
    }


def check_definition() -> dict:
    """**合成で数え方を確かめる**(周12 の教訓。実図面に当てる前に)。"""
    readings: list[Reading] = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 100.0), (250.0, 100.0)),
        ("横", (0.0, 400.0), (50.0, 400.0)),
    ]
    boxes = gaps(readings)
    return {
        "空き": len(boxes),
        "空きの形": [[round(value, 1) for value in box] for box in boxes],
        "中の点を拾う": count_in_gaps(boxes, [(100.0, 100.0)]),
        "帯の外の点は拾わない": count_in_gaps(boxes, [(100.0, 130.0)]),
        "2個入ったらちょうど1個には数えない": count_in_gaps(
            boxes, [(100.0, 100.0), (150.0, 100.0)]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="?", help="図面 PDF の場所(設定で渡す)")
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--check", action="store_true", help="合成の確かめだけ行う")
    args = parser.parse_args()
    if args.check or args.pdf is None:
        print(json.dumps(check_definition(), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(measure(args.pdf, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
