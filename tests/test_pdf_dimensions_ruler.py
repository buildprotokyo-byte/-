"""寸法線の候補が 2 本以上で落ちた数字を、選んだ基準の縮尺で 1 本に決める(K-38 やること 1)。

**合成データだけ。**実図面では、平面図 5 ページで候補 2 本以上で落ちた 33 件のうち 25 件が、
AI が選んだ基準の縮尺で 1 本に決まり、縮尺を ±2〜25% ずらすと 0〜1 件だった(K-37、共有フォルダの報告)。

守りたいこと
1. 目盛りを渡さなければ、これまでと全く同じ読み(候補が複数なら落とす)。
2. 目盛りを渡すと、「長さ×目盛り」が数字と許容差で合う候補が **1 本だけ**のときだけ拾う。
   2 本以上合う・1 本も合わないときは、これまでどおり落とす。
3. 拾った読みには「基準の縮尺で区間を選んだ」と出どころを残す(ほかの読みと混ぜない)。
4. 目盛りがずれていれば拾わない(当て推量で決まらない)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_dimensions import (
    REASON_AMBIGUOUS,
    UNIT_FROM_RULER,
    DimensionError,
    read_dimensions,
)
from tests.test_pdf_dimensions import PT_PER_MM_AT_50, _new_page

MM_PER_PT_AT_50 = 1 / PT_PER_MM_AT_50


def _two_overlapping_lines(path: Path) -> Path:
    """1 つの数字「3600」の上下に、長さの違う寸法線が 1 本ずつある紙(全長の線と連続寸法の線の間に数字がある形)。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    long_pt = 3600 * PT_PER_MM_AT_50
    short_pt = 1800 * PT_PER_MM_AT_50
    cx, y = 400.0, 300.0
    shape = page.new_shape()
    for length, dy in ((long_pt, 0.0), (short_pt, 12.0)):
        x0, x1 = cx - length / 2, cx + length / 2
        shape.draw_line(pymupdf.Point(x0, y + dy), pymupdf.Point(x1, y + dy))
        shape.draw_line(pymupdf.Point(x0, y + dy - 2), pymupdf.Point(x0, y + dy + 2))
        shape.draw_line(pymupdf.Point(x1, y + dy - 2), pymupdf.Point(x1, y + dy + 2))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    width = pymupdf.get_text_length("3600", fontname="helv", fontsize=8)
    page.insert_text(pymupdf.Point(cx - width / 2, y + 9), "3600", fontsize=8, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def test_without_a_ruler_the_ambiguous_number_is_still_dropped(tmp_path: Path) -> None:
    page = read_dimensions(_two_overlapping_lines(tmp_path / "p.pdf"), 0)
    assert not [r for r in page.readings if r.text == "3600"]
    assert [s.reason for s in page.skipped if s.text == "3600"] == [REASON_AMBIGUOUS]


def test_the_right_ruler_picks_the_one_matching_span(tmp_path: Path) -> None:
    page = read_dimensions(
        _two_overlapping_lines(tmp_path / "p.pdf"),
        0,
        ruler_mm_per_point=MM_PER_PT_AT_50,
        ruler_tolerance=0.01,
    )
    picked = [r for r in page.readings if r.text == "3600"]
    assert len(picked) == 1
    assert picked[0].value_mm == 3600.0
    assert picked[0].paper_distance_pt == pytest.approx(3600 * PT_PER_MM_AT_50, rel=1e-3)
    assert picked[0].unit_source == UNIT_FROM_RULER
    assert not [s for s in page.skipped if s.text == "3600"]


@pytest.mark.parametrize("factor", [0.8, 0.95, 1.05, 1.25])
def test_a_wrong_ruler_picks_nothing(tmp_path: Path, factor: float) -> None:
    page = read_dimensions(
        _two_overlapping_lines(tmp_path / "p.pdf"),
        0,
        ruler_mm_per_point=MM_PER_PT_AT_50 * factor,
        ruler_tolerance=0.01,
    )
    assert not [r for r in page.readings if r.text == "3600"]
    assert [s.reason for s in page.skipped if s.text == "3600"] == [REASON_AMBIGUOUS]


def test_a_ruler_needs_an_explicit_tolerance(tmp_path: Path) -> None:
    with pytest.raises(DimensionError):
        read_dimensions(_two_overlapping_lines(tmp_path / "p.pdf"), 0, ruler_mm_per_point=MM_PER_PT_AT_50)


def _plan_with_one_ambiguous_number(path: Path) -> Path:
    """曖昧な「3600」(横)と、はっきり読める縦の「2700」が 1 つずつある平面図。2700 を基準に選ぶ。"""
    from tests.test_pdf_dimensions import _v_dimension

    _two_overlapping_lines(path)
    doc = pymupdf.open(path)
    page = doc.load_page(0)
    _v_dimension(page, 150.0, 300.0, 300.0 + 2700 * PT_PER_MM_AT_50, "2700")
    page.insert_text(pymupdf.Point(700, 480), "CH=2400", fontsize=8, fontname="helv")
    out = path.with_name("plan.pdf")
    doc.save(out)
    doc.close()
    return out


def test_the_drawing_rooms_path_rereads_a_page_with_the_chosen_ruler(tmp_path: Path) -> None:
    """対応づけの JSON に「基準」(縮尺なしの読みの id)を書くと、そのページを基準の縮尺で読み直してから室を組む。"""
    import json

    import app
    from intake.drawing_room_dimensions import dimension_ids

    pdf = _plan_with_one_ambiguous_number(tmp_path / "p.pdf")
    plain = dimension_ids([read_dimensions(pdf, 0)])
    reference = next(k for k, r in plain.items() if r.text == "2700")
    reread = dimension_ids(
        [read_dimensions(pdf, 0, ruler_mm_per_point=MM_PER_PT_AT_50, ruler_tolerance=0.01)]
    )
    width_id = next(k for k, r in reread.items() if r.text == "3600")
    length_id = next(k for k, r in reread.items() if r.text == "2700")

    def run(with_ruler: bool):  # noqa: ANN202
        payload = {
            "対応づけた人": "AI(テスト)",
            "室": [{"室名": "洋室1", "横": [width_id], "縦": [length_id], "測り方": "芯々"}],
        }
        if with_ruler:
            payload["基準"] = [{"ページ": 1, "基準の寸法": reference, "許容差": 0.01}]
        rooms = tmp_path / f"rooms_{with_ruler}.json"
        rooms.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return app.run(pdf, case_id="K38-TEST", answers_path=tmp_path / "a.json",
                       drawing_rooms=rooms, build_ledger_stage=False)

    with_ruler = run(True).as_dict()["図面の寸法から組んだ室"]
    assert with_ruler["両方判明"] == ["洋室1"]
    without = run(False)
    assert without.as_dict()["図面の寸法から組んだ室"]["両方判明"] == []
    assert without.auto_confirmed_total == 0


def _dot(shape: pymupdf.Shape, x: float, y: float, r: float = 1.0) -> None:
    shape.draw_circle(pymupdf.Point(x, y), r)


def _dot_chain_crossed_by_walls(path: Path, *, dots: bool = True) -> Path:
    """両端が黒丸の寸法(450)。区間の途中を、寸法とは関係のない線(壁)が 2 本横切る。"""
    doc = pymupdf.open()
    page = _new_page(doc)
    x0, y = 300.0, 300.0
    x1 = x0 + 450 * PT_PER_MM_AT_50 * 2.0  # 1/25 相当で紙の上の長さを 10pt より長くする
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y))
    shape.finish(color=(0, 0, 0), width=0.24)
    for x in (x0 + 0.52 * (x1 - x0), x0 + 0.6 * (x1 - x0)):
        shape.draw_line(pymupdf.Point(x, y - 3), pymupdf.Point(x, y + 3))
    shape.finish(color=(1, 0, 0), width=0.72)
    if dots:
        for x in (x0, x1):
            _dot(shape, x, y)
        shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.24)
    shape.commit()
    width = pymupdf.get_text_length("450", fontname="helv", fontsize=6)
    page.insert_text(pymupdf.Point((x0 + x1) / 2 - width / 2, y - 2), "450", fontsize=6, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def test_filled_dots_mark_the_ends_even_when_a_wall_crosses_the_span(tmp_path: Path) -> None:
    """黒丸(JIS の端末記号の一つ)を目印に数え、黒丸どうしの区間を 1 本の寸法線の区間として読む。"""
    page = read_dimensions(
        _dot_chain_crossed_by_walls(tmp_path / "p.pdf"),
        0,
        ruler_mm_per_point=MM_PER_PT_AT_50 / 2.0,
        ruler_tolerance=0.01,
    )
    picked = [r for r in page.readings if r.text == "450"]
    assert len(picked) == 1
    assert picked[0].paper_distance_pt == pytest.approx(450 * PT_PER_MM_AT_50 * 2.0, rel=1e-3)


def test_without_dots_a_crossed_line_is_still_not_read(tmp_path: Path) -> None:
    page = read_dimensions(
        _dot_chain_crossed_by_walls(tmp_path / "p.pdf", dots=False),
        0,
        ruler_mm_per_point=MM_PER_PT_AT_50 / 2.0,
        ruler_tolerance=0.01,
    )
    assert not [
        r for r in page.readings
        if r.text == "450" and r.paper_distance_pt == pytest.approx(450 * PT_PER_MM_AT_50 * 2.0, rel=1e-3)
    ]


def test_dots_and_ticks_at_the_same_ends_count_as_one_span(tmp_path: Path) -> None:
    """黒丸と寸法補助線が同じ所にあっても、同じ区間を 2 度数えて「候補が複数」にしない。

    実図面(K-38)では、これを数えていなかったとき、基準の縮尺で拾えていた上側の連続寸法 5 件が落ちた。
    """
    path = _two_overlapping_lines(tmp_path / "p.pdf")
    doc = pymupdf.open(path)
    page = doc.load_page(0)
    long_pt = 3600 * PT_PER_MM_AT_50
    shape = page.new_shape()
    for x in (400.0 - long_pt / 2, 400.0 + long_pt / 2):
        _dot(shape, x, 300.0)
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.24)
    shape.commit()
    out = tmp_path / "dots.pdf"
    doc.save(out)
    doc.close()

    reading = read_dimensions(out, 0, ruler_mm_per_point=MM_PER_PT_AT_50, ruler_tolerance=0.01)
    assert [r.text for r in reading.readings if r.text == "3600"] == ["3600"]
