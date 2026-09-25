"""`axes/image_axis/pdf_dimensions.py` の回帰テスト。

このモジュールは、**図面に記入された寸法の数字**を読む経路である。
着手前に確かめた事実として、この経路はリポジトリに1つも無かった
(`docs/principles/principle_conformance_review.md` B-6)。

守りたいのは 5 つ。**どれも「読めないものを読めたことにしない」向きである。**

1. **数字だけでは寸法にならない。** どの2点の間の長さなのかが取れない数字は
   落とす。寸法線・寸法補助線(または矢印・目印)との対応が取れることを要求する。
2. **表題欄の縮尺を当てにしない**(原則3-1)。ここで作る比は、図面に記入された
   数字と、その数字が指している紙の上の距離だけから出す。
3. **単位が書かれていない数字の単位を、当てずっぽうで決めない。** 建築図面として
   成り立つ縮尺になるかどうかだけで mm と m を見分ける。どちらとも言えなければ落とす。
4. **1件だけの読みから縮尺を主張しない**(ルール3「単一の指標だけを根拠にしない」)。
5. **意味の4欄**(what / where / phase / purpose_link)を必ず付ける。

テスト用の PDF はその場で組み立てる(実図面はリポジトリに置かない決まり)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_dimensions import (
    METHOD_DIMENSION_SCALE,
    METHOD_DIMENSION_TEXT,
    REASON_INSIDE_TABLE,
    PLAUSIBLE_SCALE_MAX,
    PLAUSIBLE_SCALE_MIN,
    page_scale_from_dimensions,
    read_dimensions,
)
from axes.reading.meaning import PURPOSE_UNESTABLISHED
from tests.test_pdf_tables import draw_table

#: 実寸 1mm が 1/50 の図面で何ポイントになるか。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72

#: 突き合わせの許容差。呼び出し側が渡す約束なので、テストでも明示する。
TOLERANCE = 0.05


def _h_dimension(
    page: pymupdf.Page,
    x0: float,
    x1: float,
    y: float,
    text: str,
    *,
    ticks: bool = True,
    gap: float = 4.0,
) -> None:
    """横向きの寸法(寸法線・寸法補助線・数字)を描く。"""
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y))
    if ticks:
        shape.draw_line(pymupdf.Point(x0, y - 5), pymupdf.Point(x0, y + 5))
        shape.draw_line(pymupdf.Point(x1, y - 5), pymupdf.Point(x1, y + 5))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=8)
    page.insert_text(
        pymupdf.Point((x0 + x1) / 2 - width / 2, y - gap),
        text,
        fontsize=8,
        fontname="helv",
    )


def _v_dimension(
    page: pymupdf.Page,
    x: float,
    y0: float,
    y1: float,
    text: str,
    *,
    ticks: bool = True,
    gap: float = 4.0,
) -> None:
    """縦向きの寸法。数字は寸法線に平行(90 度回転)に入れる。"""
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x, y0), pymupdf.Point(x, y1))
    if ticks:
        shape.draw_line(pymupdf.Point(x - 5, y0), pymupdf.Point(x + 5, y0))
        shape.draw_line(pymupdf.Point(x - 5, y1), pymupdf.Point(x + 5, y1))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    length = pymupdf.get_text_length(text, fontname="helv", fontsize=8)
    page.insert_text(
        pymupdf.Point(x - gap, (y0 + y1) / 2 + length / 2),
        text,
        fontsize=8,
        fontname="helv",
        rotate=90,
    )


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=1190, height=842)  # A3 横


def test_reads_horizontal_and_vertical_dimension_numbers(tmp_path: Path) -> None:
    """寸法線と対応が取れた数字を、紙の上の距離つきで読む。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    _v_dimension(page, 150.0, 300.0, 300.0 + 2730 * PT_PER_MM_AT_50, "2730")
    path = tmp_path / "dim.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    values = sorted(reading.value_mm for reading in result.readings)
    assert values == [2730.0, 3640.0]

    by_value = {reading.value_mm: reading for reading in result.readings}
    horizontal = by_value[3640.0]
    assert horizontal.orientation == "横"
    assert horizontal.paper_distance_pt == pytest.approx(3640 * PT_PER_MM_AT_50, rel=1e-3)
    # 比は「記入された数字 ÷ 紙の上の距離」だけから出る。表題欄を読んでいない。
    assert horizontal.denominator == pytest.approx(50.0, rel=0.01)
    assert by_value[2730.0].orientation == "縦"


def test_a_number_with_no_dimension_line_is_dropped(tmp_path: Path) -> None:
    """近くに寸法線が無い数字は、拾えないものとして落とす。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(pymupdf.Point(400, 400), "3640", fontsize=8, fontname="helv")
    path = tmp_path / "stray.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert result.readings == ()
    assert any("寸法線" in item.reason for item in result.skipped)


def test_a_line_without_witness_lines_is_not_a_dimension_line(tmp_path: Path) -> None:
    """目印(寸法補助線・矢印)が両端に無い線は、寸法線として使わない。

    壁や通り芯のそばに書かれた数字を寸法として拾うと、その数字が指していない
    長さから比を作ってしまう。
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 400.0, 700.0, "3640", ticks=False)
    path = tmp_path / "bare_line.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert result.readings == ()
    assert result.skipped != ()


def test_scale_and_area_and_mark_texts_are_not_dimensions(tmp_path: Path) -> None:
    """縮尺表記・面積の記載・建具番号・階高の記号は寸法として読まない。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    for offset, text in enumerate(("1/50", "95.54", "AW-1", "GL+500", "2FL", "3")):
        y = 700.0 + offset * 20.0
        shape = page.new_shape()
        shape.draw_line(pymupdf.Point(200, y), pymupdf.Point(400, y))
        shape.draw_line(pymupdf.Point(200, y - 5), pymupdf.Point(200, y + 5))
        shape.draw_line(pymupdf.Point(400, y - 5), pymupdf.Point(400, y + 5))
        shape.finish(color=(0, 0, 0), width=0.3)
        shape.commit()
        page.insert_text(pymupdf.Point(290, y - 4), text, fontsize=8, fontname="helv")
    path = tmp_path / "noise.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    # `95.54` は数字として読めてしまうが、単位が決まらないので落ちる
    # (面積の記載なので長さではない)。ここでは1件も寸法にならないことだけ見る。
    assert result.readings == ()


def test_bare_number_unit_is_decided_only_by_plausible_scale(tmp_path: Path) -> None:
    """単位が書かれていない数字は、建築図面として成り立つ縮尺になるほうに読む。

    `3640` を mm と読めば 1/50、m と読めば 1/50000 になる。**1000 倍違う**ので、
    表題欄の縮尺を当てにしなくても見分けられる。どちらとも言えないときは落とす。
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    path = tmp_path / "bare_mm.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert [reading.value_mm for reading in result.readings] == [3640.0]
    assert result.readings[0].unit_source == "単位の表記が無く、縮尺が成り立つ側に読んだ"


def test_the_plausible_scale_range_cannot_make_mm_and_m_both_valid() -> None:
    """mm と m の両方が成り立つ範囲を認めない(1000 倍の幅を持たせない)。

    ここが 1000 倍以上に開くと、同じ数字が2通りに読めてしまい、
    上のテストの見分けが成り立たなくなる。
    """
    assert PLAUSIBLE_SCALE_MAX / PLAUSIBLE_SCALE_MIN < 1000.0


def test_an_implausible_scale_is_dropped(tmp_path: Path) -> None:
    """mm でも m でも建築図面の縮尺にならない数字は落とす。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    # 紙の上 12pt に 3640 と書かれている。mm なら約 1/8600、m なら 1/8,600,000。
    _h_dimension(page, 200.0, 212.0, 700.0, "3640")
    path = tmp_path / "implausible.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert result.readings == ()


def test_one_reading_alone_does_not_give_a_page_scale(tmp_path: Path) -> None:
    """読みが1件だけのページでは縮尺を主張しない(ルール3)。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    path = tmp_path / "single.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert len(result.readings) == 1
    assert page_scale_from_dimensions(result, tolerance=TOLERANCE) is None


def test_page_scale_needs_agreement_and_reports_outliers(tmp_path: Path) -> None:
    """一致した読みが2件以上あれば比を出す。外れた読みは除いて記録に残す。

    一致する3件の分母を**わざと少しずつ違う値**にしてある(3件目は紙の上
    910mm ぶんの線に `900` と記入されている)。全部ぴったり同じにすると、
    平均を採る作りに変えてもこのテストが気づけない。
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    _h_dimension(page, 200.0, 200.0 + 1820 * PT_PER_MM_AT_50, 740.0, "1820")
    # 許容差(±5%)の中でわずかにずれている読み。分母は約 49.45。
    _h_dimension(page, 200.0, 200.0 + 910 * PT_PER_MM_AT_50, 780.0, "900")
    # 紙の上の長さに対して数字が合っていない(記入の誤りか対応の誤り)。分母は 75。
    _h_dimension(page, 200.0, 200.0 + 910 * PT_PER_MM_AT_50, 820.0, "1365")
    path = tmp_path / "agree.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    scale = page_scale_from_dimensions(result, tolerance=TOLERANCE)
    assert scale is not None
    assert scale.denominator == pytest.approx(50.0, rel=0.01)
    assert scale.agreeing_count == 3
    assert scale.total_count == 4
    assert [round(value) for value in scale.outlier_values_mm] == [1365]
    # **平均は取らない。** 採った分母は、実在する読みのどれかの値である。
    assert scale.denominator in [reading.denominator for reading in result.readings]
    assert scale.source_text in [reading.text for reading in result.readings]


def test_two_readings_that_disagree_give_no_page_scale(tmp_path: Path) -> None:
    """一致しない2件からは、どちらも採らない(平均も取らない)。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    _h_dimension(page, 200.0, 200.0 + 1820 * PT_PER_MM_AT_50, 740.0, "2730")
    path = tmp_path / "disagree.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert len(result.readings) == 2
    assert page_scale_from_dimensions(result, tolerance=TOLERANCE) is None


def test_a_page_with_no_embedded_text_returns_nothing(tmp_path: Path) -> None:
    """スキャンしただけのページには文字が入っていないので、何も返さない。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(200, 700), pymupdf.Point(400, 700))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    path = tmp_path / "no_text.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert result.readings == ()


def test_every_reading_carries_the_four_meaning_fields(tmp_path: Path) -> None:
    """意味の4欄を必ず付ける。**目的はまだ受け取る場所が無いので未受領の印を置く。**"""
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    path = tmp_path / "meaning.pdf"
    doc.save(path)
    doc.close()

    reading = read_dimensions(path, 0).readings[0]
    meaning = reading.meaning
    assert meaning.what
    assert "ページ1" in meaning.where
    assert meaning.phase == "不明"
    assert meaning.purpose_link == PURPOSE_UNESTABLISHED
    # 4欄が揃っていないことを、値の側に残しておく。
    assert meaning.is_complete is False


def test_numbers_inside_a_ruled_table_are_not_dimensions(tmp_path: Path) -> None:
    """表の升目の中の数字は寸法にしない。**罫線は寸法線ではない。**

    これは想像上の心配ではない。建具表のページで、幅・高さ・数量の数字が
    升目の罫線と対応づけられ、**升目の長さをその数字が指す長さとして読み、
    そのページの縮尺を丸ごと誤った**(`tests/test_drawing_intake_schedules.py`
    の通しテストが落ちて見つかった)。表の数字は `schedule_tables` が読む。
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    draw_table(
        page,
        origin=(200.0, 200.0),
        col_widths=(80.0, 80.0, 80.0, 80.0),
        row_height=24.0,
        rows=(
            ("建具番号", "種別", "幅", "高さ"),
            ("WD-01", "開き戸", "780", "2000"),
            ("WD-02", "引戸", "1650", "2000"),
        ),
        caption="建具表",
    )
    path = tmp_path / "table.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert result.readings == (), "表の中の数字が寸法として読まれている"
    assert any(REASON_INSIDE_TABLE == item.reason for item in result.skipped)


def test_a_dimension_outside_a_table_is_still_read(tmp_path: Path) -> None:
    """表があるページでも、表の外の寸法は読む。**表ごと諦めない。**"""
    doc = pymupdf.open()
    page = _new_page(doc)
    draw_table(
        page,
        origin=(200.0, 200.0),
        col_widths=(80.0, 80.0),
        row_height=24.0,
        rows=(("建具番号", "幅"), ("WD-01", "780")),
        caption="建具表",
    )
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    _h_dimension(page, 200.0, 200.0 + 1820 * PT_PER_MM_AT_50, 740.0, "1820")
    path = tmp_path / "table_and_dim.pdf"
    doc.save(path)
    doc.close()

    result = read_dimensions(path, 0)
    assert sorted(reading.value_mm for reading in result.readings) == [1820.0, 3640.0]
    scale = page_scale_from_dimensions(result, tolerance=TOLERANCE)
    assert scale is not None
    assert scale.denominator == pytest.approx(50.0, rel=0.01)


def test_the_two_method_ids_are_registered_uncalibrated() -> None:
    """新しい手法は未校正で登録する。単独で自動確定の根拠にしない。"""
    from arbitration.method_policies import DEFAULT_METHOD_POLICIES

    for method_id in (METHOD_DIMENSION_TEXT, METHOD_DIMENSION_SCALE):
        policy = DEFAULT_METHOD_POLICIES[method_id]
        assert policy.calibrated is False


def test_dimension_readings_are_ordered_deterministically(tmp_path: Path) -> None:
    """同じ PDF から何度読んでも同じ順番になる(決定性を壊さない)。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    _h_dimension(page, 200.0, 200.0 + 3640 * PT_PER_MM_AT_50, 700.0, "3640")
    _h_dimension(page, 600.0, 600.0 + 1820 * PT_PER_MM_AT_50, 700.0, "1820")
    _v_dimension(page, 150.0, 300.0, 300.0 + 2730 * PT_PER_MM_AT_50, "2730")
    path = tmp_path / "order.pdf"
    doc.save(path)
    doc.close()

    first = [reading.value_mm for reading in read_dimensions(path, 0).readings]
    second = [reading.value_mm for reading in read_dimensions(path, 0).readings]
    assert first == second
    assert len(first) == 3


def test_記入寸法から出した縮尺には機械が出したと書いてある() -> None:
    """周4(2026-09-25)。**人が入れたものと混ぜない。**

    この縮尺は**人が物差しを当てた値ではなく、機械が図面の数字から出した比**である。
    根拠にそう書いていないと、人の入力と同じ重みで読まれうる。
    """
    from axes.image_axis.pdf_dimensions import SET_BY_MACHINE, DimensionScale

    scale = DimensionScale(
        denominator=50.0,
        agreeing_count=3,
        total_count=4,
        outlier_values_mm=(1.0,),
        source_text="3000",
    )
    assert scale.set_by == SET_BY_MACHINE
    assert scale.provenance()["set_by"] == SET_BY_MACHINE


def test_機械が出したという欄に人の名前は入らない() -> None:
    """**この欄は書き換えられる口を持たない。**定数を返すだけにしてある。"""
    from axes.image_axis.pdf_dimensions import SET_BY_MACHINE

    assert "機械" in SET_BY_MACHINE
    assert "人" not in SET_BY_MACHINE
