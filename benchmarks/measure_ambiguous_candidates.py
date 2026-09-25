"""周25(空間): **「候補が複数」で落ちた数字は、縮尺を手がかりに絞れるか**を数える。

**この道具は数えるだけで、本番の経路には繋いでいない。**
`_pick_span` にも `ANCHOR_TOUCH_PT` にも手を入れていない(K-29)。

周24 で、**空き 26 個すべてに読めなかった数字が入っている**ことと、
**最多の理由が「寸法線の候補が複数あり、どれを指しているか決まらない」**
(重複を除いて 25 件)であることが分かった。周24 はこう書いて止めた。

> **`_pick_span` を直す根拠にしない。**
> **「候補が複数」で落ちた 25 件のうち、正しい候補が選べるものが
> いくつあるかは数えていない。**

**この案件に寸法の正解データは無いので、「正しい候補が選べたか」は
原理的に測れない。**測れるのは**「候補を 1 つに絞る手がかりが、既にある
データの中にあるか」**だけである。

**手がかりの限界を先に書く。**縮尺は**同じ PDF の同じページの寸法から
出している。別のデータ源ではない。**だから**ここで 1 つに絞れても、
独立した 2 つ目の証言にはならず、階層1(自動確定)には永久に届かない。**

基準は `docs/loop_round25_ambiguous_candidates_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axes.image_axis import pdf_dimensions as pd  # noqa: E402
from benchmarks.measure_height_destination import ceiling_notes  # noqa: E402

#: 縮尺を出すときの許容差。**この周のための選び方であって、
#: リポジトリの許容差の決定ではない**(v8 10章12項は未決)。
SCALE_TOLERANCE = 0.01

#: 候補の長さが数字と「合う」とみなす差。
MATCH_TOLERANCE = 0.01

#: 線2(対照)の合格。読めた寸法の 9 割以上が合うこと。
CONTROL_SHARE = 0.9


def candidate_lengths(pdf_path: Path, page_index: int) -> list[tuple[str, list[float]]]:
    """「候補が複数」で落ちた横向きの数字と、その候補の区間の長さ。

    **`read_dimensions` と同じ手順をたどる。**表の除外も #139 の枠外しも
    同じものを通す。**判定には触らず、落ちる手前の中身を見るだけ。**
    """
    with pymupdf.open(pdf_path) as document:
        page = document.load_page(page_index)
        segments = pd._collect_segments(page)
        numbers = pd._number_texts(page)
        page_area = page.rect.width * page.rect.height

    tables = tuple(
        region.rect_pt
        for region in pd.find_tables(pdf_path, page_index)
        if not pd._looks_like_a_drawing_frame(region, page_area)
    )
    if tables:
        segments = [
            segment
            for segment in segments
            if not pd._in_any_table(
                (
                    min(segment[0][0], segment[1][0]),
                    min(segment[0][1], segment[1][1]),
                    max(segment[0][0], segment[1][0]),
                    max(segment[0][1], segment[1][1]),
                ),
                tables,
            )
        ]
    spans = pd._spans(segments)

    out: list[tuple[str, list[float]]] = []
    for number in numbers:
        if pd._in_any_table(number.rect_pt, tables):
            continue
        matches = pd._matching_spans(number, spans)
        if len(matches) < 2:
            continue
        if pd._pick_span(matches) is not None:
            continue
        if pd._orientation(matches[0].segment) != "横":
            continue
        out.append((number.text, sorted(span.length for span in matches)))
    return out


def _value_mm(text: str) -> float | None:
    """数字の文字から、そのまま読んだ値(ミリメートル)。**計算しない。**"""
    match = pd._NUMBER_RE.match(text)
    if match is None:
        return None
    digits = match.group("number").replace(",", "")
    fraction = match.group("fraction")
    raw = float(digits + ("." + fraction if fraction else ""))
    unit = match.group("unit")
    if unit is not None:
        return raw * pd._UNIT_FACTORS[unit.lower()]
    if len(digits) < pd.MIN_BARE_DIGITS:
        return None
    return raw


def matching_candidates(
    value_mm: float, lengths: list[float], mm_per_point: float
) -> int:
    """その値と ±1% で合う候補がいくつあるか。"""
    return sum(
        1
        for length in lengths
        if length > 0.0
        and abs(length * mm_per_point - value_mm) <= MATCH_TOLERANCE * value_mm
    )


def _by_orientation(page: "pd.DimensionPage", mm_per_point: float) -> dict[str, dict[str, int]]:
    """**あとから足した診断。**向きごとに、縮尺と合う読みの数。"""
    out: dict[str, dict[str, int]] = {}
    for reading in page.readings:
        row = out.setdefault(reading.orientation, {"読み": 0, "合う": 0})
        row["読み"] += 1
        if abs(
            reading.paper_distance_pt * mm_per_point - reading.value_mm
        ) <= MATCH_TOLERANCE * reading.value_mm:
            row["合う"] += 1
    return out


def measure_page(pdf_path: Path, page_index: int, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        page = document[page_index]
        if not ceiling_notes(page):
            return {"ページ": page_index + 1, "平面図とみなす": False}
    finally:
        document.close()

    got = pd.read_dimensions(pdf_path, page_index)
    scale = pd.page_scale_from_dimensions(got, tolerance=SCALE_TOLERANCE)
    if scale is None:
        return {
            "ページ": page_index + 1,
            "平面図とみなす": True,
            "縮尺が出ない": True,
        }
    mm_per_point = scale.mm_per_point

    ambiguous = candidate_lengths(pdf_path, page_index)
    values = [_value_mm(text) for text, _ in ambiguous]
    usable = [
        (value, lengths)
        for value, (_, lengths) in zip(values, ambiguous)
        if value is not None
    ]
    real = [matching_candidates(value, lengths, mm_per_point) for value, lengths in usable]

    rng = random.Random(seed + page_index)
    shuffled = [value for value, _ in usable]
    rng.shuffle(shuffled)
    decoy = [
        matching_candidates(value, lengths, mm_per_point)
        for value, (_, lengths) in zip(shuffled, usable)
    ]

    control = sum(
        1
        for reading in got.readings
        if abs(reading.paper_distance_pt * mm_per_point - reading.value_mm)
        <= MATCH_TOLERANCE * reading.value_mm
    )

    sizes: dict[str, int] = {"2個": 0, "3個": 0, "4個以上": 0}
    for _, lengths in ambiguous:
        key = "2個" if len(lengths) == 2 else "3個" if len(lengths) == 3 else "4個以上"
        sizes[key] += 1

    return {
        "ページ": page_index + 1,
        "平面図とみなす": True,
        "縮尺の分母": round(scale.denominator, 3),
        "候補が複数で落ちた横": len(ambiguous),
        "線1_ちょうど1つ合う": sum(1 for value in real if value == 1),
        "線1_囮": sum(1 for value in decoy if value == 1),
        "線1_1つも合わない": sum(1 for value in real if value == 0),
        "線1_2つ以上合う": sum(1 for value in real if value >= 2),
        "線2_対照_読めた寸法": len(got.readings),
        "線2_対照_合う": control,
        # **あとから足した診断の欄。**線の判定には使っていない。
        # 足した理由は、対照が落ちたときに「縮尺が外れている」のか
        # 「読めた寸法どうしが食い違っている」のかを分けないと、
        # 落ちた意味が言えないため。**先に決めた線は動かしていない。**
        "参考_縮尺に一致した読み": scale.agreeing_count,
        "参考_向きごとの食い違い": _by_orientation(got, mm_per_point),
        "線3_候補の数": sizes,
    }


def measure(pdf_path: Path, seed: int) -> dict:
    document = pymupdf.open(pdf_path)
    try:
        pages = document.page_count
    finally:
        document.close()
    rows = [measure_page(pdf_path, index, seed) for index in range(pages)]
    plan = [row for row in rows if row.get("平面図とみなす") and not row.get("縮尺が出ない")]

    def total(key: str) -> int:
        return sum(row[key] for row in plan)

    ambiguous = total("候補が複数で落ちた横")
    one = total("線1_ちょうど1つ合う")
    decoy = total("線1_囮")
    readings = total("線2_対照_読めた寸法")
    control = total("線2_対照_合う")
    sizes = {
        key: sum(row["線3_候補の数"][key] for row in plan)
        for key in ("2個", "3個", "4個以上")
    }
    by_orientation: dict[str, dict[str, int]] = {}
    for row in plan:
        for orientation, counts in row["参考_向きごとの食い違い"].items():
            here = by_orientation.setdefault(orientation, {"読み": 0, "合う": 0})
            here["読み"] += counts["読み"]
            here["合う"] += counts["合う"]
    works = control >= CONTROL_SHARE * readings if readings else False

    return {
        "ページ": plan,
        "縮尺が出なかったページ": [
            row["ページ"] for row in rows if row.get("縮尺が出ない")
        ],
        "参考_縮尺に一致した読み": total("参考_縮尺に一致した読み"),
        "参考_向きごとの食い違い": by_orientation,
        "線2_対照_数え方は働いているか": {
            "読めた寸法": readings,
            "合う": control,
            "合格": f"{CONTROL_SHARE:.0%} 以上",
            "働いている": works,
        },
        "線1_縮尺を手がかりに候補は絞れるか": {
            "候補が複数で落ちた横": ambiguous,
            "ちょうど1つ合う": one,
            "囮(値だけ入れ替え)": decoy,
            "1つも合わない": total("線1_1つも合わない"),
            "2つ以上合う": total("線1_2つ以上合う"),
            "合格": "過半、かつ囮より多い",
            "通過": (one * 2 > ambiguous and one > decoy) if ambiguous else False,
            "読んでよいか": works,
            "但し書き": (
                "縮尺は同じ PDF の同じページから出している。別のデータ源ではないので、"
                "1 つに絞れても独立した 2 つ目の証言にはならず、階層1 には届かない"
            ),
        },
        "線3_候補の数": {
            "内訳": sizes,
            "判定": "置いていない(記述であって通過・不通過の線ではない)",
        },
    }


def check_definition() -> dict:
    """**合成で数え方を確かめる**(周12 の教訓。実図面に当てる前に)。"""
    # 1pt = 17.64mm(1/50)。3640mm は 206.35pt。
    mm_per_point = 17.64
    return {
        "ちょうど合う候補を拾う": matching_candidates(
            3640.0, [206.35, 120.0], mm_per_point
        ),
        "1%_の外は拾わない": matching_candidates(
            3640.0, [210.0, 120.0], mm_per_point
        ),
        "2つとも合えば2と数える": matching_candidates(
            3640.0, [206.35, 206.4], mm_per_point
        ),
        "値が読めない文字は None": _value_mm("AW-1"),
        "2桁は落とす": _value_mm("90"),
        "単位つきは直す": _value_mm("3.64m"),
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
