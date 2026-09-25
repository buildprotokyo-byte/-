"""図面に記入された寸法から、室の縦・横・天井高を組む(K-37 やること 1)。**合成データだけ。**

基準は `docs/k37_dimensions_to_quantities_criteria.md`(測る前にコミット済み)。

守りたいこと
1. 室の長さは、**機械が読んだ寸法の id とその足し算だけ**から組む。引き算・按分・よくある値で埋めない。
2. 縦横が揃わない室は面積を出さない。「片方だけ判明」として残す。
3. どの室にも結び付かなかった寸法は「対応先不明」として残す。捨てない。
4. 知らない id・向きの合わない id は使わず、理由を残す。
5. 天井高は、そのページに `CH=` の値が印字されているときだけ使う。
6. 出どころは「図面の寸法」で、**人の入力と混ぜない。**校正済みにならない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pytest

from arbitration.method_policies import DEFAULT_METHOD_POLICIES
from estimating.from_room_dimensions import (
    KIND_FLOOR_AREA,
    KIND_WALL_AREA,
    ORIGIN_DRAWING,
    quantities_from_room_dimensions,
)
from intake.drawing_room_dimensions import (
    METHOD_DRAWING_ROOM_DIMENSIONS,
    STATE_BOTH,
    STATE_NONE,
    STATE_ONE,
    RoomAssignment,
    dimension_ids,
    rooms_from_drawing,
)


@dataclass(frozen=True)
class _Reading:
    text: str
    value_mm: float
    orientation: str
    page_number: int = 8


@dataclass(frozen=True)
class _Page:
    page_number: int
    readings: tuple[_Reading, ...]


READINGS = {
    "P8-D1": _Reading("1,925", 1925.0, "横"),
    "P8-D2": _Reading("1,485", 1485.0, "縦"),
    "P8-D3": _Reading("1,690", 1690.0, "横"),
    "P8-D4": _Reading("500", 500.0, "横"),
    "P8-D5": _Reading("400", 400.0, "横"),
    "P8-D6": _Reading("8,245", 8245.0, "縦"),
}
PAGE_TEXTS = {8: "書斎\nCH=2530\nFL±0\n洗面室 CH=2230"}


def _assign(room, width=(), length=(), **kwargs) -> RoomAssignment:  # noqa: ANN001
    return RoomAssignment(
        room_name=room,
        width_ids=tuple(width),
        length_ids=tuple(length),
        assigned_by="AI(テスト)",
        **kwargs,
    )


def test_ids_follow_the_reading_order_on_each_page() -> None:
    pages = [_Page(8, (READINGS["P8-D1"], READINGS["P8-D2"])), _Page(9, (READINGS["P8-D3"],))]
    ids = dimension_ids(pages)
    assert list(ids) == ["P8-D1", "P8-D2", "P9-D1"]


def test_both_sides_from_read_values_only() -> None:
    result = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D2"], area_basis="内法", ceiling_height_mm=2530.0)],
        READINGS,
        PAGE_TEXTS,
    )
    room = result.rooms[0]
    assert result.states["書斎"] == STATE_BOTH
    assert (room.width_mm, room.length_mm, room.ceiling_height_mm) == (1925.0, 1485.0, 2530.0)
    assert room.area_basis == "内法"
    assert "図面の寸法" in room.entered_by and "P8-D1" in room.entered_by


def test_ids_are_added_never_subtracted() -> None:
    result = rooms_from_drawing(
        [_assign("ＷＩＣ", ["P8-D4", "P8-D5"], ["P8-D2"])], READINGS, PAGE_TEXTS
    )
    assert result.rooms[0].width_mm == 900.0


def test_one_side_only_keeps_the_room_without_an_area() -> None:
    result = rooms_from_drawing([_assign("洗面室", ["P8-D3"], [])], READINGS, PAGE_TEXTS)

    assert result.states["洗面室"] == STATE_ONE
    quantities = quantities_from_room_dimensions(result.rooms, origin=ORIGIN_DRAWING)
    assert not [q for q in quantities.quantities if q.target.startswith(KIND_FLOOR_AREA)]
    assert any("洗面室" in gap for gap in quantities.gaps)


def test_a_room_with_nothing_is_unknown_and_not_invented() -> None:
    result = rooms_from_drawing([_assign("廊下")], READINGS, PAGE_TEXTS)
    assert result.states["廊下"] == STATE_NONE
    assert result.rooms == ()


def test_unreferenced_dimensions_are_kept_as_unresolved() -> None:
    result = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D2"])], READINGS, PAGE_TEXTS
    )
    assert set(result.unresolved) == {"P8-D3", "P8-D4", "P8-D5", "P8-D6"}


def test_an_unknown_id_is_not_used_and_the_reason_is_kept() -> None:
    result = rooms_from_drawing(
        [_assign("書斎", ["P8-D99"], ["P8-D2"])], READINGS, PAGE_TEXTS
    )
    assert result.states["書斎"] == STATE_ONE
    assert any("P8-D99" in gap for gap in result.gaps)


def test_a_reading_in_the_wrong_direction_is_not_used() -> None:
    result = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D3"])], READINGS, PAGE_TEXTS  # D3 は横
    )
    assert result.states["書斎"] == STATE_ONE
    assert any("P8-D3" in gap and "向き" in gap for gap in result.gaps)


def test_ceiling_height_is_used_only_when_printed_on_the_page() -> None:
    printed = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D2"], ceiling_height_mm=2530.0, ceiling_page=8)],
        READINGS,
        PAGE_TEXTS,
    )
    not_printed = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D2"], ceiling_height_mm=2400.0, ceiling_page=8)],
        READINGS,
        PAGE_TEXTS,
    )
    assert printed.rooms[0].ceiling_height_mm == 2530.0
    assert not_printed.rooms[0].ceiling_height_mm is None
    assert any("CH=2400" in gap for gap in not_printed.gaps)


def test_quantities_carry_the_drawing_method_not_the_human_one() -> None:
    result = rooms_from_drawing(
        [_assign("書斎", ["P8-D1"], ["P8-D2"], ceiling_height_mm=2530.0)], READINGS, PAGE_TEXTS
    )
    quantities = quantities_from_room_dimensions(result.rooms, origin=ORIGIN_DRAWING)

    kinds = {q.target.split("::")[0] for q in quantities.quantities}
    assert KIND_WALL_AREA in kinds
    for q in quantities.quantities:
        assert q.method_id == METHOD_DRAWING_ROOM_DIMENSIONS
        assert q.source_kind != "human"
    assert DEFAULT_METHOD_POLICIES[METHOD_DRAWING_ROOM_DIMENSIONS].calibrated is False


def _plan_with_dimensions(path: Path) -> Path:
    from tests.test_app_one_pass import FINISH_ROWS
    from tests.test_pdf_dimensions import PT_PER_MM_AT_50, _h_dimension, _v_dimension
    from tests.test_pdf_tables import draw_table

    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    draw_table(page, origin=(80.0, 120.0), col_widths=(120.0, 90.0, 90.0, 180.0),
               row_height=24.0, rows=FINISH_ROWS, caption="内装仕上表")
    page.insert_text(pymupdf.Point(700, 480), "CH=2400", fontsize=8, fontname="helv")
    x0, y0 = 600.0, 600.0
    _h_dimension(page, x0, x0 + 3600 * PT_PER_MM_AT_50, y0, "3600")
    _v_dimension(page, 560.0, 300.0, 300.0 + 2700 * PT_PER_MM_AT_50, "2700")
    doc.save(path)
    doc.close()
    return path


def test_the_one_pass_puts_drawing_room_quantities_on_the_finish_lines(tmp_path: Path) -> None:
    import app
    from axes.image_axis.pdf_dimensions import read_dimensions

    pdf = _plan_with_dimensions(tmp_path / "plan.pdf")
    page = read_dimensions(pdf, 0)
    ids = dimension_ids([page])
    width_id = next(k for k, r in ids.items() if r.orientation == "横")
    length_id = next(k for k, r in ids.items() if r.orientation == "縦")
    rooms = tmp_path / "rooms.json"
    rooms.write_text(
        json.dumps(
            {
                "対応づけた人": "AI(テスト)",
                "室": [
                    {"室名": "洋室1", "横": [width_id], "縦": [length_id],
                     "測り方": "芯々", "天井高_mm": 2400, "天井高のページ": 1}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = app.run(pdf, case_id="K37-TEST", answers_path=tmp_path / "a.json",
                     drawing_rooms=rooms, build_ledger_stage=False)

    floor = next(line for line in result.lines if line.work_item.startswith("床"))
    assert floor.quantity == pytest.approx(9.72)
    assert any("図面の寸法" in note for note in floor.notes)
    assert floor.waits_for_human is False
    summary = result.as_dict()["図面の寸法から組んだ室"]
    assert summary["両方判明"] == ["洋室1"]
    assert result.auto_confirmed_total == 0
