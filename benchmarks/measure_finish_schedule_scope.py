"""K-05: 内装仕上表から読んだ工事の有無を、区分ごとに数える。

基準は `docs/k05_finish_schedule_scope_criteria.md`(**測る前にコミット済み**)。

使い方
------
合成の仕上表だけで測る(図面は要らない)::

    python -m benchmarks.measure_finish_schedule_scope

実図面にも当てる::

    python -m benchmarks.measure_finish_schedule_scope --pdf <PDFのパス> --pages 3 4

**図面のパスは引数で渡す。** リポジトリに書かない(取り決め④)。
**--pdf を付けたときに印字するのは件数と区分だけ**で、室名・材料名・品番は
出さない。行の中身が要るときは `--show-unassigned` を付ける
(**その出力はリポジトリにも記憶にも書かない**)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from axes.image_axis.pdf_tables import TableCell, TableRegion
from axes.image_axis.schedule_tables import read_finish_schedules
from estimating.finish_schedule_scope import (
    READINGS,
    assign_finish_schedule_scope,
)

RESULT_PATH = Path("docs/k05_finish_schedule_scope_result.json")

#: 合成の仕上表。**読み方の表の 6 通りと、下地欄が空欄の継続行 2 通り。**
SYNTHETIC_ROWS: tuple[tuple[str, ...], ...] = (
    ("室名", "部位", "下地", "仕上", "メーカー"),
    ("架空室A", "床", "既存", "既存", ""),
    ("架空室A", "壁", "既存", "架空クロス", "架空社"),
    ("架空室A", "天井", "交換", "架空クロス", "架空社"),
    ("架空室A", "巾木", "ー", "", ""),
    ("架空室A", "廻縁", "既存", "", ""),
    ("架空室B", "腰壁", "架空ボード12mm", "架空タイル", "架空社"),
    ("", "", "", "架空クロス", "架空社 別の面"),
    ("", "", "", "", "架空社 付属部材"),
)


def _fake_table(rows: tuple[tuple[str, ...], ...]) -> TableRegion:
    """PDF を作らずに升目だけ組む。**読み方の表の検査に PDF は要らない。**"""
    cells = tuple(
        tuple(
            TableCell(
                text=text,
                rect_pt=(
                    float(c) * 10,
                    float(r) * 10,
                    float(c) * 10 + 9,
                    float(r) * 10 + 9,
                ),
                row_index=r,
                col_index=c,
                merged_with_above=False,
            )
            for c, text in enumerate(row)
        )
        for r, row in enumerate(rows)
    )
    return TableRegion(
        page_index=0,
        rect_pt=(0.0, 0.0, 100.0, 100.0),
        rows=cells,
        caption=None,
    )


def _measure_synthetic() -> dict[str, Any]:
    from axes.image_axis.schedule_tables import _as_finish_schedule

    schedule = _as_finish_schedule(_fake_table(SYNTHETIC_ROWS))
    if schedule is None:
        return {"error": "合成の表が内装仕上表として読めませんでした"}
    result = assign_finish_schedule_scope(schedule)
    items = [item for a in result.assignments for item in a.items]
    return {
        "rows": result.row_count,
        "items": result.item_count,
        "counts_by_reading": result.counts_by_reading(),
        "counts_by_work_kind": result.counts_by_work_kind(),
        "questions": len(result.questions),
        "unassigned": len(result.unassigned),
        "quantities": sum(1 for item in items if item.value_range is not None),
        "with_page_and_row": sum(
            1 for item in items if item.evidence and item.evidence[0].page_number >= 1
        ),
    }


def _measure_pdf(pdf: Path, pages: tuple[int, ...]) -> dict[str, Any]:
    totals = {reading: 0 for reading in READINGS}
    kinds: dict[str, int] = {}
    rows = items = questions = unassigned = quantities = with_evidence = 0
    unassigned_rows: list[dict[str, Any]] = []
    per_page: list[dict[str, Any]] = []

    for page_number in pages:
        for schedule in read_finish_schedules(pdf, page_number - 1):
            result = assign_finish_schedule_scope(schedule)
            rows += result.row_count
            items += result.item_count
            questions += len(result.questions)
            unassigned += len(result.unassigned)
            for row in result.unassigned:
                unassigned_rows.append(row.as_dict())
            for reading, count in result.counts_by_reading().items():
                totals[reading] += count
            for kind, count in result.counts_by_work_kind().items():
                kinds[kind] = kinds.get(kind, 0) + count
            for assignment in result.assignments:
                for item in assignment.items:
                    if item.value_range is not None:
                        quantities += 1
                    if item.evidence:
                        with_evidence += 1
            per_page.append(
                {
                    "page_number": schedule.page_number,
                    "rows": result.row_count,
                    "items": result.item_count,
                    "base_columns": list(schedule.base_columns),
                    "counts_by_reading": result.counts_by_reading(),
                }
            )

    return {
        "rows": rows,
        "items": items,
        "counts_by_reading": totals,
        "counts_by_work_kind": kinds,
        "questions": questions,
        "unassigned": unassigned,
        "quantities": quantities,
        "with_evidence": with_evidence,
        "per_page": per_page,
        "_unassigned_rows": unassigned_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--pages", type=int, nargs="+", default=(3, 4))
    parser.add_argument("--show-unassigned", action="store_true")
    parser.add_argument("--write", action="store_true", help="結果を docs へ書く")
    args = parser.parse_args()

    out: dict[str, Any] = {"synthetic": _measure_synthetic()}
    print("== 合成の仕上表 ==")
    print(json.dumps(out["synthetic"], ensure_ascii=False, indent=2))

    if args.pdf is not None:
        measured = _measure_pdf(args.pdf, tuple(args.pages))
        unassigned_rows = measured.pop("_unassigned_rows")
        out["real_drawing"] = measured
        print("== 実図面 ==")
        print(json.dumps(measured, ensure_ascii=False, indent=2))
        if args.show_unassigned and unassigned_rows:
            print("== 割り当てられなかった行(この出力は保存しない) ==")
            print(json.dumps(unassigned_rows, ensure_ascii=False, indent=2))

    if args.write:
        RESULT_PATH.write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"書きました: {RESULT_PATH}")


if __name__ == "__main__":
    main()
