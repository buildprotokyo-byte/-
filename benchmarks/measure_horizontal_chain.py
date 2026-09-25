"""周23(空間): **横向きの寸法 29 件が、なぜ連なりにならないのか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`pdf_dimensions` の定数(`ANCHOR_TOUCH_PT` を含む)は 1 つも動かしていない。

周22 で、**縦の連なりは本物だった**のに、**横の「連なり」は同じ x 区間に
重なって置かれた寸法 2 本**だった。**格子の縦線が 2 本しかできない原因はそこ。**
周22 の報告に**測っていない予想を 2 つ**書き残したので、ここで測る。

**「同じ寸法線に乗っている」をどう見分けたか(近似であることを先に書く)**

`DimensionReading` は、その読みが乗っていた `_Span.segment` を持っていない。
そこで**同じ直線に乗っているか(共線)を 0.5pt で見分ける。**
**これは近似である。**ほぼ平行な別の寸法線が 0.5pt 以内に重なっていれば、
同じ線として数える。**この案件でそれがどれだけ起きるかは測っていない。**

基準は `docs/loop_round23_horizontal_chain_criteria.md`(測る前にコミット済み)。
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

#: 線1・線2 の合格。横向きの読みのうち、相手を持つものの件数。
LINE1_MIN = 10
LINE2_MIN = 10

#: 線3 の合格。紙の上半分・下半分それぞれの件数。
LINE3_MIN = 5

#: 同じ寸法線(同じ直線)に乗っているとみなす、線からの距離(ポイント)。
SAME_LINE_PT = 0.5

#: 同じ高さに並んでいるとみなす `y` の差(ポイント)。
#: **周22 の連なりと同じ 5pt。**揃っていることと繋がっていることは別。
SAME_LEVEL_PT = 5.0

Reading = tuple[str, tuple[float, float], tuple[float, float]]


def _distance_to_line(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """点から**直線**(線分ではない)への距離。"""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0.0:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    return abs(dy * (point[0] - start[0]) - dx * (point[1] - start[1])) / length


def same_line(first: Reading, second: Reading, tolerance: float = SAME_LINE_PT) -> bool:
    """2 つの読みが**同じ寸法線**に乗っているか。**共線で見分ける近似。**"""
    if first[0] != second[0]:
        return False
    return all(
        _distance_to_line(point, first[1], first[2]) <= tolerance
        for point in (second[1], second[2])
    )


def with_a_partner_on_the_same_line(readings: list[Reading]) -> int:
    """**同じ寸法線に、ほかの読みが乗っている**ものの件数。"""
    return sum(
        1
        for index, reading in enumerate(readings)
        if any(
            same_line(reading, other)
            for other_index, other in enumerate(readings)
            if other_index != index
        )
    )


def _level(reading: Reading) -> float:
    """その読みの高さ(2 点の y の真ん中)。"""
    return (reading[1][1] + reading[2][1]) / 2.0


def with_a_partner_at_the_same_level(
    readings: list[Reading], tolerance: float = SAME_LEVEL_PT
) -> int:
    """**同じ高さ(`y` が近い)に、ほかの読みがある**ものの件数。"""
    levels = [_level(reading) for reading in readings]
    return sum(
        1
        for index, level in enumerate(levels)
        if any(
            abs(level - other) <= tolerance
            for other_index, other in enumerate(levels)
            if other_index != index
        )
    )


def scatter_levels(readings: list[Reading], height: float, seed: int) -> list[Reading]:
    """囮: **x と長さはそのままに、高さだけを紙の中でばらばらに置き直す。**

    **0 を返すしかない囮ではない。**読みは 5〜9 件/ページ、紙の高さは 842pt
    なので、**でたらめでも 5pt 以内に揃う組はときどき出る。**
    """
    rng = random.Random(seed)
    out: list[Reading] = []
    for orientation, start, end in readings:
        delta = end[1] - start[1]
        y = rng.uniform(0.0, max(height - abs(delta), 1.0))
        out.append((orientation, (start[0], y), (end[0], y + delta)))
    return out


def halves(readings: list[Reading], height: float) -> dict[str, int]:
    """紙の上半分・下半分それぞれの件数(**予想2 を測る**)。"""
    middle = height / 2.0
    upper = sum(1 for reading in readings if _level(reading) < middle)
    return {"上半分": upper, "下半分": len(readings) - upper}


def gaps_on_the_same_line(readings: list[Reading]) -> dict[str, int]:
    """**あとから足した診断の欄。**線1〜線3 の判定には使っていない。

    足した理由: 線1 が通った(同じ寸法線に相手がいる)のに、
    **周22 では連なりが 2 本しか無かった。**その食い違いを言うには、
    **同じ線に乗る読みどうしが隣り合っているのか、間が空いているのか**を
    分ける必要がある。**隣り合っていれば端点が一致して連なりになる。**
    """
    out = {"隣り合う(隙間 5pt 未満)": 0, "間が空いている": 0}
    for index, reading in enumerate(readings):
        for other_index in range(index + 1, len(readings)):
            other = readings[other_index]
            if not same_line(reading, other):
                continue
            first = sorted([reading[1][0], reading[2][0]])
            second = sorted([other[1][0], other[2][0]])
            gap = max(second[0] - first[1], first[0] - second[1])
            key = "隣り合う(隙間 5pt 未満)" if gap < 5.0 else "間が空いている"
            out[key] += 1
    return out


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
    """1 ページ分。**寸法の値そのものは返さない。件数だけ。**"""
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
            return {"ページ": page_index + 1, "平面図とみなす": False}
        height = page.rect.height
    finally:
        document.close()

    got = read_dimensions(pdf_path, page_index)
    readings: list[Reading] = [
        (reading.orientation, reading.start_pt, reading.end_pt)
        for reading in got.readings
    ]
    yoko = [reading for reading in readings if reading[0] == "横"]
    tate = [reading for reading in readings if reading[0] == "縦"]
    decoy = scatter_levels(yoko, height, seed + page_index)

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        "横の寸法": len(yoko),
        "縦の寸法": len(tate),
        "線1_同じ寸法線に相手がいる横": with_a_partner_on_the_same_line(yoko),
        "線1_対照_同じ寸法線に相手がいる縦": with_a_partner_on_the_same_line(tate),
        "線2_同じ高さに相手がいる横": with_a_partner_at_the_same_level(yoko),
        "線2_囮": with_a_partner_at_the_same_level(decoy),
        "線3_上下の分かれ方": halves(yoko, height),
        "参考_同じ寸法線に乗る横どうしの並び": gaps_on_the_same_line(yoko),
        "参考_同じ寸法線に乗る縦どうしの並び": gaps_on_the_same_line(tate),
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす")]

    def total(key: str) -> int:
        return sum(row[key] for row in plan)

    yoko = total("横の寸法")
    line1 = total("線1_同じ寸法線に相手がいる横")
    control = total("線1_対照_同じ寸法線に相手がいる縦")
    line2 = total("線2_同じ高さに相手がいる横")
    decoy = total("線2_囮")
    upper = sum(row["線3_上下の分かれ方"]["上半分"] for row in plan)
    lower = sum(row["線3_上下の分かれ方"]["下半分"] for row in plan)

    def pairs(key: str) -> dict[str, int]:
        return {
            label: sum(row[key][label] for row in plan)
            for label in ("隣り合う(隙間 5pt 未満)", "間が空いている")
        }

    return {
        "ページ": plan,
        "参考_同じ寸法線に乗る横どうしの並び": pairs("参考_同じ寸法線に乗る横どうしの並び"),
        "参考_同じ寸法線に乗る縦どうしの並び": pairs("参考_同じ寸法線に乗る縦どうしの並び"),
        "横の寸法": yoko,
        "縦の寸法": total("縦の寸法"),
        "線1_横の寸法線に区間は複数あるか": {
            "本物": line1,
            "合格": LINE1_MIN,
            "通過(予想が外れた)": line1 >= LINE1_MIN,
            "対照_縦": control,
            "数え方が働いている": control >= 2,
            "囮": "置いていない(対照は縦向きの同じ数え方)",
        },
        "線2_横の寸法は同じ高さに並んでいるか": {
            "本物": line2,
            "囮": decoy,
            "合格": LINE2_MIN,
            "通過": line2 >= LINE2_MIN and line2 > decoy,
            "但し書き": "揃っていることと端点が繋がっていることは別。繋がりは周22 で 2 本",
        },
        "線3_紙の上下に分かれているか": {
            "上半分": upper,
            "下半分": lower,
            "合格": LINE3_MIN,
            "予想2が支持される": upper >= LINE3_MIN and lower >= LINE3_MIN,
            "囮": "置いていない(置かれ方の記述であって位置を当てる線ではない)",
        },
    }


def check_definition() -> dict:
    """**合成で数え方を確かめる**(周12 の教訓。実図面に当てる前に)。"""
    on_one_line: list[Reading] = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (80.0, 100.0), (130.0, 100.0)),
        ("横", (0.0, 400.0), (50.0, 400.0)),
    ]
    same_level: list[Reading] = [
        ("横", (0.0, 100.0), (50.0, 100.0)),
        ("横", (200.0, 103.0), (250.0, 103.0)),
        ("横", (0.0, 400.0), (50.0, 400.0)),
    ]
    return {
        "同じ直線に乗る2件を拾う": with_a_partner_on_the_same_line(on_one_line),
        "3pt ずれていても同じ高さと数える": with_a_partner_at_the_same_level(same_level),
        "3pt ずれていたら同じ直線とは数えない": with_a_partner_on_the_same_line(
            same_level
        ),
        "上下の分かれ方": halves(on_one_line, 800.0),
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
