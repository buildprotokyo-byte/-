"""周34 の測定: **凡例が裏付けた「工事の区分」の印は、数量に届くか。**

基準は `docs/loop_round34_work_marks_reach_criteria.md`(**測る前にコミット済み。
結果を見てから変えていない**)。

前の周(周33)との違いは**出典ひとつ**である。周33 は「位相らしい語」を**こちらが 12 個選んで**
当てた。ここでは**図面自身が凡例で名乗っている印**(K-20 の対照表の `work_marks`)を使う。
**測り方・距離の段・囮の作り方は周33 とまったく同じ**にして、比べられるようにしてある。

**凡例の印そのもの(記号と意味の対)は 1 文字も出さない**(K-20・K-22・K-33)。
出すのは件数・割合・ページ番号・距離だけ。

実行::

    .venv/bin/python -m benchmarks.measure_work_mark_reach \
        --pdf <匿名化v2.pdf> --answers <回答.json> --table <対照表.json> --out r34.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fitz

from axes.image_axis.legend_lookup import LegendTable, normalize
from benchmarks.measure_phase_word_proximity import (
    DECOY_DRAWS,
    DISTANCES_MM,
    LINE1_MARGIN,
    LINE1_MIN_SHARE,
    Point,
    decoy_shares,
    distances_for,
    normalise,
    page_of,
    points_of,
    share_within,
)

#: 周33 の数字。**報告に並べて載せるためだけに持つ。判定には使わない。**
ROUND33_REAL = {10.0: 0.1438, 25.0: 0.3166, 50.0: 0.4868}
ROUND33_DECOY = {10.0: 0.1671, 25.0: 0.3870, 50.0: 0.6097}


def work_mark_codes(table: LegendTable) -> frozenset[str]:
    """対照表の「工事の区分」の印。**意味のほうは読まない。**"""
    return frozenset(
        normalize(str(row.get("code", "")))
        for row in table.work_marks
        if row.get("code") and row.get("meaning")
    )


def split_words(page: Any, codes: frozenset[str]) -> tuple[list[Point], list[Point]]:
    """(工事の区分の印の中心, それ以外の印字語の中心)。**語そのものは返さない。**"""
    marks: list[Point] = []
    others: list[Point] = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        token = normalise(text)
        if not token.strip():
            continue
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        if normalize(token) in codes:
            marks.append(centre)
        else:
            others.append(centre)
    return marks, others


def pages_with(points_by_page: Mapping[int, Sequence[Point]]) -> set[int]:
    return {page for page, points in points_by_page.items() if points}


def measure(pdf: Path, answers: Path, table_path: Path, seed: int) -> tuple[dict, dict]:
    from intake.drawing_intake import IntakeConfig, read_drawing

    table = LegendTable.load(table_path)
    codes = work_mark_codes(table)

    marks_by_page: dict[int, list[Point]] = {}
    others_by_page: dict[int, list[Point]] = {}
    with fitz.open(pdf) as doc:
        for index, page in enumerate(doc, start=1):
            marks, others = split_words(page, codes)
            marks_by_page[index] = marks
            others_by_page[index] = others

    intake = read_drawing(
        IntakeConfig(case_id="round34", pdf_path=pdf, answers_path=answers)
    )
    everything = list(quantities(intake))
    located = [item for item in everything if points_of(item) and page_of(item)]
    total = len(located)

    real = distances_for(located, marks_by_page)
    wanted = {page: len(points) for page, points in marks_by_page.items()}
    decoys = decoy_shares(located, others_by_page, wanted, seed)
    real_shares = {limit: share_within(real, limit, total) for limit in DISTANCES_MM}

    narrow = DISTANCES_MM[0]
    line1 = (
        real_shares[narrow] >= LINE1_MIN_SHARE
        and (real_shares[narrow] - decoys[narrow]) >= LINE1_MARGIN
    )

    quantity_pages = {
        page for page in marks_by_page if any(page_of(i) == page for i in located)
    }
    mark_pages = pages_with(marks_by_page)
    marks_on_quantity_pages = sum(
        len(marks_by_page[page]) for page in mark_pages & quantity_pages
    )

    result = {
        "対照表の工事の区分の印": len(codes),
        "入口の数量": len(everything),
        "座標を持つ数量": total,
        "工事の区分の印が刷られている箇所": sum(len(v) for v in marks_by_page.values()),
        "それ以外の印字語": sum(len(v) for v in others_by_page.values()),
        "線1_いちばん狭い10mmで届くか": {
            "本物": round(real_shares[narrow], 4),
            "囮": round(decoys[narrow], 4),
            "差": round(real_shares[narrow] - decoys[narrow], 4),
            "合格": (
                f"本物 {LINE1_MIN_SHARE:.0%} 以上、かつ囮との差 {LINE1_MARGIN:.0%} 以上"
                "(周33 とまったく同じ条件)"
            ),
            "通過": line1,
        },
        "線2_囮_3段とも": {
            f"{limit:g}mm": {
                "本物": round(real_shares[limit], 4),
                "囮": round(decoys[limit], 4),
                "差": round(real_shares[limit] - decoys[limit], 4),
                "周33の本物": ROUND33_REAL[limit],
                "周33の囮": ROUND33_DECOY[limit],
            }
            for limit in DISTANCES_MM
        },
        "線3_ページが重なるか": {
            "断り": "合格・不通過の線ではない。どこで途切れているかを見る欄",
            "印が出るページ数": len(mark_pages),
            "数量が出るページ数": len(quantity_pages),
            "両方のページ数": len(mark_pages & quantity_pages),
            "印だけのページ数": len(mark_pages - quantity_pages),
            "数量だけのページ数": len(quantity_pages - mark_pages),
            "数量が出るページに載っている印の箇所": marks_on_quantity_pages,
        },
        "線4_本番の判定に触っていないか": {
            "読むだけの測定": True,
            "自動確定した件数": sum(
                1 for item in everything if getattr(item, "is_confirmed", False)
            ),
            "合格": "判定を 1 か所も変えていないこと",
        },
    }
    detail = {
        "断り": "**共有フォルダだけに置く。リポジトリには入れない。**",
        "ページごとの印の箇所数": {
            str(page): len(points) for page, points in sorted(marks_by_page.items())
        },
        "ページごとの数量": {
            str(page): sum(1 for i in located if page_of(i) == page)
            for page in sorted(marks_by_page)
        },
    }
    return result, detail


def quantities(intake: Any) -> tuple[Any, ...]:
    from estimating.from_intake import quantities_from_intake

    return quantities_from_intake(intake)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detail-out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()

    result, detail = measure(args.pdf, args.answers, args.table, args.seed)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.detail_out is not None:
        args.detail_out.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
