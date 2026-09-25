"""機械の検算 2 つ(K-40 1 番)。**合成データだけ。**

1. 室の長方形(対応づけた横と縦の寸法の範囲)の中に、**別の室名**が入っていたら知らせる。
   実図面(K-38)では LDK の長方形に玄関とホールの室名が入っていて、面積が大きすぎた。
2. **同じ室の同じ部位を、同じ工事で 2 行以上数えていたら**知らせる。
   実図面(K-38)では LDK の壁にクロスとタイルが両方とも壁全面の面積で載っていた。

どちらも**知らせるだけ**で、数量は変えない(判定のしかたを変えない)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from estimating.cross_checks import same_surface_counted_twice
from intake.drawing_room_dimensions import RoomAssignment, other_room_names_inside


@dataclass(frozen=True)
class _Reading:
    text: str
    value_mm: float
    orientation: str
    start_pt: tuple[float, float]
    end_pt: tuple[float, float]


READINGS = {
    "P8-D1": _Reading("5,000", 5000.0, "横", (100.0, 50.0), (300.0, 50.0)),
    "P8-D2": _Reading("4,000", 4000.0, "縦", (50.0, 100.0), (50.0, 260.0)),
}


def _ldk(**kw) -> RoomAssignment:  # noqa: ANN003
    return RoomAssignment(room_name="LDK", width_ids=("P8-D1",), length_ids=("P8-D2",), **kw)


def test_another_room_name_inside_the_rectangle_is_reported() -> None:
    labels = {"玄関": [(8, 200.0, 150.0)], "LDK": [(8, 150.0, 200.0)], "書斎": [(8, 400.0, 150.0)]}
    warnings = other_room_names_inside([_ldk()], READINGS, labels)
    assert len(warnings) == 1
    assert "LDK" in warnings[0] and "玄関" in warnings[0]
    assert "書斎" not in warnings[0]


def test_a_name_on_another_page_or_a_room_with_one_side_is_not_checked() -> None:
    labels = {"玄関": [(9, 200.0, 150.0)]}
    assert other_room_names_inside([_ldk()], READINGS, labels) == ()
    one_side = RoomAssignment(room_name="LDK", width_ids=("P8-D1",))
    assert other_room_names_inside([one_side], READINGS, {"玄関": [(8, 200.0, 150.0)]}) == ()


def _row(place: str, item: str, quantity: float | None, unit: str = "㎡") -> dict:
    return {"場所": place, "工事項目": item, "数量": quantity, "単位": unit}


def test_two_new_finishes_on_the_same_wall_are_reported() -> None:
    warnings = same_surface_counted_twice(
        [
            _row("LDK", "壁 ビニルクロスＣ 新設", 72.03),
            _row("LDK", "壁 タイル 新設", 72.03),
            _row("書斎", "壁 ビニルクロスＣ 新設", 17.25),
        ]
    )
    assert len(warnings) == 1
    assert "LDK" in warnings[0] and "壁" in warnings[0] and "2 行" in warnings[0]


def test_removal_and_new_finish_on_the_same_wall_are_not_double_counting() -> None:
    rows = [_row("LDK", "壁 撤去", 72.03), _row("LDK", "壁 タイル 新設", 72.03)]
    assert same_surface_counted_twice(rows) == ()


def test_lines_without_a_quantity_are_not_counted() -> None:
    rows = [_row("LDK", "壁 ビニルクロスＣ 新設", 72.03), _row("LDK", "壁 タイル 新設", None)]
    assert same_surface_counted_twice(rows) == ()


def test_the_one_pass_reports_both_checks(tmp_path: Path) -> None:
    import app
    from axes.image_axis.pdf_dimensions import read_dimensions
    from intake.drawing_room_dimensions import dimension_ids
    from tests.test_drawing_room_dimensions import _plan_with_dimensions

    pdf = _plan_with_dimensions(tmp_path / "plan.pdf")
    doc = pymupdf.open(pdf)
    doc.load_page(0).insert_text(pymupdf.Point(700, 400), "玄関", fontsize=8, fontname="japan")
    labelled = tmp_path / "labelled.pdf"
    doc.save(labelled)
    doc.close()

    ids = dimension_ids([read_dimensions(labelled, 0)])
    width_id = next(k for k, r in ids.items() if r.orientation == "横")
    length_id = next(k for k, r in ids.items() if r.orientation == "縦")
    rooms = tmp_path / "rooms.json"
    rooms.write_text(
        json.dumps(
            {
                "対応づけた人": "AI(テスト)",
                "室": [
                    {"室名": "洋室1", "横": [width_id], "縦": [length_id], "測り方": "芯々"},
                    {"室名": "玄関", "横": [], "縦": []},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = app.run(labelled, case_id="K40-TEST", answers_path=tmp_path / "a.json",
                     drawing_rooms=rooms, build_ledger_stage=False)

    checks = result.as_dict()["検算"]
    assert any("洋室1" in c and "玄関" in c for c in checks["長方形の中の別の室名"])
    assert isinstance(checks["同じ面を2回以上"], list)
    assert result.auto_confirmed_total == 0
