"""`axes/image_axis/schedule_tables.py` の回帰テスト。

**テスト用の PDF はこの中で組み立てる合成のベクター PDF である。**

この層がやるのは、`pdf_tables` が返した升目に**意味**を与えることだけ
(どの列が建具番号か、この表は建具表か内装仕上表か)。守りたいのは 6 つ。

1. 列見出しから役割を決めること。**列の順番を決め打ちしない。**
   建具表の列の並びは事務所ごとに違う。
2. 役割が揃わない表を建具表・内装仕上表と名乗らないこと。
   図面には凡例表・面積表・記事欄など、罫線で組まれた別の表がいくらでもある。
3. **単位が表に書かれていない寸法を mm と決めないこと。** 図面に印字された
   文字列はそのまま残し、mm に直した値は単位が読めたときだけ出す。
4. **数量は整数として読めたときだけ出すこと。** 読めなければ None にして
   理由を残す。0 で埋めると「建具が無い」と区別がつかなくなる。
5. 内装仕上表の**縦に結合された室名を、下の行へ引き継ぐこと**。
   ただし**引き継いだことを記録に残す**(結合と空欄は別物として扱う)。
6. すべての値に**ページ番号と座標と元の文字列**が付いて回ること。

この層は数量を確定させない。読んだ値は根拠つきの証拠として
`intake/drawing_intake.py` が仲裁層へ渡すだけである。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.schedule_tables import (
    METHOD_DOOR_SCHEDULE,
    METHOD_FINISH_SCHEDULE,
    read_door_schedules,
    read_finish_schedules,
)
from tests.test_pdf_tables import draw_table, scanned_pdf, single_table_pdf


def _pdf(
    path: Path,
    rows: tuple[tuple[str | None, ...], ...],
    *,
    caption: str | None = None,
    col_widths: tuple[float, ...] | None = None,
) -> Path:
    return single_table_pdf(path, rows, caption=caption, col_widths=col_widths)


DOOR_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("建具番号", "種別", "幅", "高さ", "数量"),
    ("WD-01", "引戸", "1650", "2000", "2"),
    ("WD-02", "折戸", "1200", "2000", "1"),
    ("WD-03", "開き戸", "780", "2000", "3"),
)

DOOR_ROWS_WITH_UNIT: tuple[tuple[str | None, ...], ...] = (
    ("記号", "種別", "幅(mm)", "高さ(mm)", "員数"),
    ("AD-1", "引戸", "1650", "2000", "2"),
)


# ---------------------------------------------------------------------------
# 1. 列見出しから役割を決める
# ---------------------------------------------------------------------------


def test_a_door_schedule_is_recognised_from_its_column_headings(tmp_path: Path) -> None:
    schedules = read_door_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)

    assert len(schedules) == 1
    schedule = schedules[0]
    assert [row.mark for row in schedule.rows] == ["WD-01", "WD-02", "WD-03"]
    assert [row.kind for row in schedule.rows] == ["引戸", "折戸", "開き戸"]
    assert [row.quantity for row in schedule.rows] == [2, 1, 3]


def test_the_column_order_is_not_assumed(tmp_path: Path) -> None:
    """列を入れ替えても同じ結果になること。"""
    shuffled: tuple[tuple[str | None, ...], ...] = (
        ("数量", "高さ", "建具番号", "幅", "種別"),
        ("2", "2000", "WD-01", "1650", "引戸"),
    )
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", shuffled), 0)[0]

    assert schedule.rows[0].mark == "WD-01"
    assert schedule.rows[0].quantity == 2
    assert schedule.rows[0].kind == "引戸"


def test_alternative_headings_are_accepted(tmp_path: Path) -> None:
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS_WITH_UNIT), 0)[0]

    assert schedule.rows[0].mark == "AD-1"
    assert schedule.rows[0].quantity == 2


# ---------------------------------------------------------------------------
# 2. 建具表でない表を建具表と呼ばない
# ---------------------------------------------------------------------------


def test_a_table_without_a_mark_column_is_not_a_door_schedule(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("項目", "面積", "備考"),
        ("専有延床面積", "95.54", ""),
    )
    assert read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0) == []


def test_a_mark_column_alone_is_not_enough(tmp_path: Path) -> None:
    """建具番号の列があるだけの表(凡例など)を建具表にしない。"""
    rows: tuple[tuple[str | None, ...], ...] = (
        ("記号", "備考"),
        ("WD-01", "既存撤去"),
    )
    assert read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0) == []


def test_one_supporting_column_is_not_enough(tmp_path: Path) -> None:
    """記号と種別だけの 2 列(凡例)を建具表にしない。

    `DOOR_MIN_SUPPORTING_COLUMNS` を 1 に緩めるとここが落ちる。
    """
    rows: tuple[tuple[str | None, ...], ...] = (
        ("記号", "種別"),
        ("WD-01", "引戸"),
    )
    assert read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0) == []


def test_an_interior_finish_table_is_not_read_as_a_door_schedule(
    tmp_path: Path,
) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "部位", "仕上"),
        ("洋室1", "床", "フローリング"),
    )
    assert read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0) == []


def test_a_scanned_page_yields_no_schedules(tmp_path: Path) -> None:
    path = scanned_pdf(tmp_path / "a.pdf")
    assert read_door_schedules(path, 0) == []
    assert read_finish_schedules(path, 0) == []


# ---------------------------------------------------------------------------
# 3. 単位を勝手に決めない
# ---------------------------------------------------------------------------


def test_a_width_without_a_stated_unit_is_kept_as_printed(tmp_path: Path) -> None:
    """単位が書かれていない `1650` を mm と決めない。

    住宅の建具なら mm だろうという推測は、たいてい当たる。だが
    「たいてい当たる推測で単位を埋める」ことが、2026-09-21 に直した
    単位の取り違えと同じ形の事故を作る。
    """
    row = read_door_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)[0].rows[0]

    assert row.width_text == "1650"
    assert row.width_mm is None
    assert any("単位" in note for note in row.notes)


def test_a_width_is_converted_when_the_heading_states_the_unit(tmp_path: Path) -> None:
    row = read_door_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS_WITH_UNIT), 0)[0].rows[0]

    assert row.width_text == "1650"
    assert row.width_mm == pytest.approx(1650.0)
    assert row.height_mm == pytest.approx(2000.0)


def test_the_unit_may_come_from_the_caption(tmp_path: Path) -> None:
    schedule = read_door_schedules(
        _pdf(tmp_path / "a.pdf", DOOR_ROWS, caption="建具表（単位:mm）"), 0
    )[0]

    assert schedule.rows[0].width_mm == pytest.approx(1650.0)
    assert schedule.unit_source == "caption"


def test_metres_in_the_heading_are_converted_not_ignored(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("記号", "種別", "幅(m)", "高さ(m)", "数量"),
        ("AD-1", "引戸", "1.65", "2.00", "2"),
    )
    row = read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0].rows[0]

    assert row.width_mm == pytest.approx(1650.0)


# ---------------------------------------------------------------------------
# 4. 数量
# ---------------------------------------------------------------------------


def test_a_quantity_with_a_counter_word_is_read(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("記号", "種別", "幅", "数量"),
        ("AD-1", "引戸", "1650", "2ヶ所"),
    )
    assert read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0].rows[0].quantity == 2


def test_an_unreadable_quantity_is_none_with_a_reason(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("記号", "種別", "幅", "数量"),
        ("AD-1", "引戸", "1650", "別途"),
    )
    row = read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0].rows[0]

    assert row.quantity is None
    assert any("別途" in note for note in row.notes)


def test_a_missing_quantity_column_leaves_every_quantity_none(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ"),
        ("WD-01", "引戸", "1650", "2000"),
    )
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0]

    assert schedule.rows[0].quantity is None
    assert "数量" not in schedule.columns


def test_a_row_without_a_mark_is_dropped_and_recorded(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "2"),
        ("", "", "", "", ""),
    )
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0]

    assert [row.mark for row in schedule.rows] == ["WD-01"]
    assert len(schedule.skipped_rows) == 1


def test_a_repeated_mark_is_not_merged_and_not_summed(tmp_path: Path) -> None:
    """同じ建具番号が 2 行あっても足さない。図面がそう書いているという事実を残す。"""
    rows: tuple[tuple[str | None, ...], ...] = (
        ("建具番号", "種別", "幅", "高さ", "数量"),
        ("WD-01", "引戸", "1650", "2000", "2"),
        ("WD-01", "引戸", "1650", "2000", "3"),
    )
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", rows), 0)[0]

    assert [row.quantity for row in schedule.rows] == [2, 3]


# ---------------------------------------------------------------------------
# 5. 内装仕上表
# ---------------------------------------------------------------------------


FINISH_LONG: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室1", "床", "フローリング"),
    (None, "壁", "ビニルクロス"),
    (None, "天井", "ビニルクロス"),
    ("浴室", "床", "タイル"),
)


def test_a_long_form_finish_table_is_read(tmp_path: Path) -> None:
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", FINISH_LONG, col_widths=(110.0, 80.0, 160.0)), 0
    )[0]

    assert schedule.layout == "long"
    assert [(row.room, row.part, row.finish) for row in schedule.rows] == [
        ("洋室1", "床", "フローリング"),
        ("洋室1", "壁", "ビニルクロス"),
        ("洋室1", "天井", "ビニルクロス"),
        ("浴室", "床", "タイル"),
    ]


def test_a_carried_forward_room_name_is_marked_as_such(tmp_path: Path) -> None:
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", FINISH_LONG, col_widths=(110.0, 80.0, 160.0)), 0
    )[0]

    assert [row.room_source for row in schedule.rows] == [
        "cell",
        "merged_cell",
        "merged_cell",
        "cell",
    ]


def test_a_blank_room_name_is_distinguished_from_a_merged_one(tmp_path: Path) -> None:
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "部位", "仕上"),
        ("洋室1", "床", "フローリング"),
        ("", "壁", "ビニルクロス"),
    )
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", rows, col_widths=(110.0, 80.0, 160.0)), 0
    )[0]

    assert schedule.rows[1].room == "洋室1"
    assert schedule.rows[1].room_source == "blank_carried_forward"
    assert any("空欄" in note for note in schedule.rows[1].notes)


FINISH_WIDE: tuple[tuple[str | None, ...], ...] = (
    ("室名", "床", "壁", "天井"),
    ("洋室1", "フローリング", "ビニルクロス", "ビニルクロス"),
    ("浴室", "タイル", "タイル", "塗装"),
)


def test_a_wide_form_finish_table_is_flattened_into_triples(tmp_path: Path) -> None:
    """部位が列になっている書き方(こちらのほうが図面では多い)。"""
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", FINISH_WIDE, col_widths=(110.0, 140.0, 140.0, 140.0)), 0
    )[0]

    assert schedule.layout == "wide"
    assert [(row.room, row.part, row.finish) for row in schedule.rows] == [
        ("洋室1", "床", "フローリング"),
        ("洋室1", "壁", "ビニルクロス"),
        ("洋室1", "天井", "ビニルクロス"),
        ("浴室", "床", "タイル"),
        ("浴室", "壁", "タイル"),
        ("浴室", "天井", "塗装"),
    ]


def test_an_empty_finish_cell_produces_no_row(tmp_path: Path) -> None:
    """仕上げが書かれていない部位を「仕上げ無し」として作らない。"""
    rows: tuple[tuple[str | None, ...], ...] = (
        ("室名", "床", "壁", "天井"),
        ("洋室1", "フローリング", "", "ビニルクロス"),
    )
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", rows, col_widths=(110.0, 140.0, 140.0, 140.0)), 0
    )[0]

    assert [row.part for row in schedule.rows] == ["床", "天井"]


def test_a_door_schedule_is_not_read_as_a_finish_table(tmp_path: Path) -> None:
    assert read_finish_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0) == []


# ---------------------------------------------------------------------------
# 6. 根拠(ページ番号・座標・元の文字列)
# ---------------------------------------------------------------------------


def test_every_door_row_carries_its_position_and_source_text(tmp_path: Path) -> None:
    schedule = read_door_schedules(_pdf(tmp_path / "a.pdf", DOOR_ROWS), 0)[0]
    row = schedule.rows[0]

    assert row.page_number == 1
    mark_cell = row.cells["建具番号"]
    assert mark_cell.source_text == "WD-01"
    x0, y0, x1, y1 = mark_cell.rect_pt
    assert x1 > x0 and y1 > y0
    assert row.provenance()["page_number"] == 1
    assert row.provenance()["cells"]["数量"]["source_text"] == "2"


def test_a_merged_room_points_at_the_cell_that_holds_the_text(tmp_path: Path) -> None:
    """引き継いだ室名の座標は、文字が書いてある結合元のセルを指すこと。"""
    schedule = read_finish_schedules(
        _pdf(tmp_path / "a.pdf", FINISH_LONG, col_widths=(110.0, 80.0, 160.0)), 0
    )[0]

    assert schedule.rows[1].cells["室名"].rect_pt == schedule.rows[0].cells["室名"].rect_pt


def test_the_method_ids_are_stable(tmp_path: Path) -> None:
    """手法IDは `arbitration/method_policies.py` の鍵なので、勝手に変えない。"""
    assert METHOD_DOOR_SCHEDULE == "pdf_table_door_schedule"
    assert METHOD_FINISH_SCHEDULE == "pdf_table_finish_schedule"


def test_the_page_number_follows_the_page_that_was_read(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=842, height=595)
    page = doc.new_page(width=842, height=595)
    draw_table(
        page,
        origin=(60.0, 120.0),
        col_widths=(90.0,) * 5,
        row_height=24.0,
        rows=DOOR_ROWS,
    )
    path = tmp_path / "a.pdf"
    doc.save(path)
    doc.close()

    schedule = read_door_schedules(path, 1)[0]
    assert schedule.rows[0].page_number == 2
