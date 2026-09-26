"""K-42: 改装平面の色つきの注記と、既存撤去図の青い網の道。**合成の PDF だけを使う。**

守りたいこと

1. 赤・青の注記を 1 行ずつ読み、色と工事の別(凡例が名乗る候補)を付ける。黒い文字は読まない。
2. 数量は、注記に**数が刷られているときだけ**。刷られていなければ ``None``(0 にしない)。
   同じ注記が何行あるかは知らせで、数量にしない。
3. 室ごとの仕上の早見表と表題欄の文字は読まない。
4. 青い網は面にして、縮尺どおりの面積(±2%)。黒い網は測らない。縮尺が無ければ面積を出さない。
5. 面ごとに「床組 撤去」「天井組 撤去」の 2 行。凡例の文が無ければ根拠にそう書く。
6. 許容の少し外のもの(色・傾き)は捨てずに「候補(近いが外れ)」に残す。
7. **自動確定は 0 件のまま。**
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

import app
from intake import demolition_hatch as hatch
from intake import plan_colour_notes as notes
from tests.test_pdf_tables import draw_table

W, H = 1190.0, 842.0
RED = (1.0, 0.0, 0.0)
BLUE = (0.0, 0.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
MM_PER_PT_1_50 = 25.4 / 72.0 * 50.0


def _text(page: pymupdf.Page, x: float, y: float, text: str, colour=BLACK, size: float = 9.0) -> None:
    page.insert_text(pymupdf.Point(x, y), text, fontname="japan", fontsize=size, color=colour)


# ---------------------------------------------------------------------------
# 合成の図面
# ---------------------------------------------------------------------------


def _plan_page(page: pymupdf.Page) -> None:
    """改装平面の合成。室名 2 つ、赤・青・黒の注記、早見表、表題欄。"""
    _text(page, 900, 800, "改装平面図")  # 表題欄(下端 12%)
    _text(page, 1000, 800, "1/50")
    _text(page, 300, 300, "洋室1")
    _text(page, 700, 300, "書斎")
    _text(page, 290, 330, "可動棚6枚", RED)
    _text(page, 290, 360, "床見切新設", RED)
    _text(page, 690, 330, "床見切新設", RED)
    _text(page, 690, 360, "照明移設", BLUE)
    _text(page, 290, 390, "既存棚", BLACK)  # 黒は既存。読まない
    _text(page, 690, 390, "水栓交換", (0.9, 0.1, 0.05))  # 許容の内(ちょうど 0xff0000 でなくてよい)
    _text(page, 690, 420, "近い赤", (0.8, 0.4, 0.35))  # 許容の外だが近い → 候補(近いが外れ)
    # 室ごとの仕上の早見表(内装仕上表と同じ中身なので読まない)
    _text(page, 950, 480, "洋室1")
    _text(page, 950, 495, "天井：")
    _text(page, 980, 495, "貼替", RED)
    _text(page, 950, 508, "床　：")
    _text(page, 980, 508, "重張", RED)
    # 表題欄の赤い文字も読まない
    _text(page, 600, 800, "表題の赤", RED)


def _segment_in_rect(c: float, sign: int, rect: tuple[float, float, float, float]):
    """直線 y = sign*x + c と四角の交わる線分。"""
    x0, y0, x1, y1 = rect
    points = []
    for x in (x0, x1):
        y = sign * x + c
        if y0 - 1e-9 <= y <= y1 + 1e-9:
            points.append((x, y))
    for y in (y0, y1):
        x = (y - c) / sign
        if x0 - 1e-9 <= x <= x1 + 1e-9:
            points.append((x, y))
    if len(points) < 2:
        return None
    points.sort()
    return points[0], points[-1]


def _cross_hatch(page: pymupdf.Page, rect, colour, spacing: float = 10.0) -> None:
    shape = page.new_shape()
    x0, y0, x1, y1 = rect
    for sign in (1, -1):
        cs = [y - sign * x for x in (x0, x1) for y in (y0, y1)]
        c = math.floor(min(cs))
        while c <= max(cs):
            seg = _segment_in_rect(c, sign, rect)
            if seg is not None and math.dist(*seg) > 1.0:
                shape.draw_line(pymupdf.Point(*seg[0]), pymupdf.Point(*seg[1]))
            c += spacing
    shape.finish(color=colour, width=0.5, closePath=False)
    shape.commit()


BLUE_RECT = (300.0, 200.0, 500.0, 400.0)  # 200pt × 200pt
BLACK_RECT = (650.0, 200.0, 800.0, 350.0)


def _demolition_page(page: pymupdf.Page, *, scale: bool = True, legend: bool = True) -> None:
    _text(page, 900, 800, "既存撤去図")
    if scale:
        _text(page, 1000, 800, "1/50")
    _cross_hatch(page, BLUE_RECT, BLUE)
    _cross_hatch(page, BLACK_RECT, BLACK)
    _text(page, 390, 300, "洋室1")
    if legend:
        _text(page, 100, 600, "m. 床組、天井組撤去範囲")


def _single_line(page: pymupdf.Page, a, b, colour) -> None:
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(*a), pymupdf.Point(*b))
    shape.finish(color=colour, width=0.5, closePath=False)
    shape.commit()


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def plan_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    _plan_page(doc.new_page(width=W, height=H))
    return _save(doc, tmp_path / "plan.pdf")


# ---------------------------------------------------------------------------
# 1. 改装平面の注記
# ---------------------------------------------------------------------------


def _read(pdf: Path) -> notes.PlanNotesResult:
    labels = notes.room_label_positions(pdf, 1, ["洋室１", "書斎"])
    return notes.read_colour_notes(pdf, 1, labels)


def test_the_plan_page_is_chosen_by_its_title(plan_pdf: Path) -> None:
    assert notes.pages_with_title(plan_pdf, "改装平面") == [1]
    assert notes.pages_with_title(plan_pdf, "撤去") == []


def test_coloured_notes_are_read_with_colour_and_kind(plan_pdf: Path) -> None:
    result = _read(plan_pdf)
    got = {(n.text, n.place): n for n in result.notes}

    assert set(got) == {
        ("可動棚6枚", "洋室１"),
        ("床見切新設", "洋室１"),
        ("床見切新設", "書斎"),
        ("照明移設", "書斎"),
        ("水栓交換", "書斎"),
    }
    assert got[("可動棚6枚", "洋室１")].colour == "赤"
    assert got[("可動棚6枚", "洋室１")].kind == "新設/交換"
    assert got[("可動棚6枚", "洋室１")].colour_value == "#ff0000"
    assert got[("照明移設", "書斎")].colour == "青"
    assert got[("照明移設", "書斎")].kind == "移設/脱着"
    # 許容の内の少しずれた赤も赤。色の値はそのまま残す
    assert got[("水栓交換", "書斎")].colour == "赤"
    assert got[("水栓交換", "書斎")].colour_value != "#ff0000"


def test_black_text_quick_table_and_title_block_are_not_notes(plan_pdf: Path) -> None:
    result = _read(plan_pdf)
    texts = {n.text for n in result.notes}

    assert "既存棚" not in texts
    assert not texts & {"貼替", "重張", "表題の赤"}
    assert result.excluded_quick_table == 2
    # 早見表の見出しの室名は、注記の場所に使わない
    assert result.labels_used == 2


def test_a_printed_count_becomes_the_quantity_and_none_otherwise(plan_pdf: Path) -> None:
    got = {(n.text, n.place): n for n in _read(plan_pdf).notes}

    shelf = got[("可動棚6枚", "洋室１")]
    assert shelf.count == 6.0
    assert shelf.count_unit == "枚"
    for key in (("床見切新設", "洋室１"), ("照明移設", "書斎")):
        assert got[key].count is None  # 刷られていない → None。0 にしない
        assert got[key].count_unit is None


def test_same_text_count_is_reported_but_is_not_a_quantity(plan_pdf: Path) -> None:
    got = {(n.text, n.place): n for n in _read(plan_pdf).notes}

    edge = got[("床見切新設", "洋室１")]
    assert edge.same_text_count == 2
    assert set(edge.same_text_places) == {"洋室１", "書斎"}
    assert edge.count is None


def test_a_colour_near_but_outside_tolerance_is_kept_as_a_near_miss(plan_pdf: Path) -> None:
    result = _read(plan_pdf)

    assert "近い赤" not in {n.text for n in result.notes}
    (miss,) = result.near_misses
    assert miss.text == "近い赤"
    assert "赤に近い" in miss.reason


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [
        ((1.0, 0.0, 0.0), ("赤", None)),
        ((0.85, 0.2, 0.1), ("赤", None)),
        ((0.1, 0.15, 0.9), ("青", None)),
        ((1.0, 0.5, 0.5), (None, "赤")),
        ((0.3, 0.3, 0.8), (None, "青")),
        ((0.0, 0.0, 0.0), (None, None)),
        ((1.0, 0.0, 1.0), (None, None)),  # 紫はどちらにも近くない
    ],
)
def test_colour_tolerance(rgb, expected) -> None:
    assert notes.classify_colour(rgb) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("可動棚6枚", (6.0, "枚", "6枚")),
        ("ﾀｵﾙﾊﾞｰ取付2ヶ所", (2.0, "箇所", "2ヶ所")),
        ("床見切新設", (None, None, None)),
        ("W1955*H2300", (None, None, None)),
        ("棚2枚・金物4個", (None, None, "2枚・4個")),  # どれが数量か選ばない
    ],
)
def test_printed_count(text, expected) -> None:
    assert notes.printed_count(text) == expected


# ---------------------------------------------------------------------------
# 2. 既存撤去図の青い網
# ---------------------------------------------------------------------------


def _hatch_pdf(tmp_path: Path, **kwargs) -> Path:
    doc = pymupdf.open()
    _demolition_page(doc.new_page(width=W, height=H), **kwargs)
    return _save(doc, tmp_path / "demolition.pdf")


def test_blue_hatch_area_matches_the_scale_within_two_percent(tmp_path: Path) -> None:
    pdf = _hatch_pdf(tmp_path)
    labels = notes.room_label_positions(pdf, 1, ["洋室１"])
    result = hatch.measure_demolition_hatch(
        pdf, 1, mm_per_point=MM_PER_PT_1_50, room_labels=labels, legend_text="x"
    )

    (region,) = result.regions  # 黒い網は測らない
    x0, y0, x1, y1 = BLUE_RECT
    expected = (x1 - x0) * (y1 - y0) * (MM_PER_PT_1_50 / 1000.0) ** 2
    assert region.area_sqm == pytest.approx(expected, rel=0.02)
    assert region.places == ("洋室１",)


def test_black_hatch_alone_gives_no_region(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    _cross_hatch(page, BLACK_RECT, BLACK)
    pdf = _save(doc, tmp_path / "black.pdf")

    result = hatch.measure_demolition_hatch(pdf, 1, mm_per_point=MM_PER_PT_1_50)
    assert result.regions == []
    assert result.segments_used == 0


def test_without_a_scale_the_region_has_no_area(tmp_path: Path) -> None:
    pdf = _hatch_pdf(tmp_path, scale=False)
    result = hatch.measure_demolition_hatch(pdf, 1, mm_per_point=None)

    (region,) = result.regions
    assert region.area_sqm is None
    assert region.area_pt2 > 0


def test_hatch_slope_and_colour_tolerance_and_near_misses(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    # 35 度の線・少しずれた青: 許容の内
    for i in range(12):
        _single_line(page, (100 + i * 8, 100), (100 + i * 8 + 80, 100 + 80 * math.tan(math.radians(35))), (0.1, 0.1, 0.9))
    # 25 度の青: 傾きが近いが外れ
    _single_line(page, (600, 100), (700, 100 + 100 * math.tan(math.radians(25))), BLUE)
    # 青に近い色の 45 度: 色が近いが外れ
    _single_line(page, (600, 300), (650, 350), (0.3, 0.3, 0.8))
    # 水平の青: 網ではない(近くもない)
    _single_line(page, (600, 500), (800, 500), BLUE)
    pdf = _save(doc, tmp_path / "tolerance.pdf")

    result = hatch.measure_demolition_hatch(pdf, 1, mm_per_point=MM_PER_PT_1_50)
    assert result.segments_used == 12
    reasons = [m["理由"] for m in result.near_misses]
    assert any("傾き" in r for r in reasons)
    assert any("青に近い" in r for r in reasons)
    assert len([r for r in reasons if "傾き" in r or "青に近い" in r]) == 2


def test_legend_text_is_found_or_reported_missing(tmp_path: Path) -> None:
    assert hatch.find_legend_text(_hatch_pdf(tmp_path)) is not None
    other = tmp_path / "nolegend"
    other.mkdir()
    assert hatch.find_legend_text(_hatch_pdf(other, legend=False)) is None


# ---------------------------------------------------------------------------
# 3. app.py の道として
# ---------------------------------------------------------------------------

FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "下地", "仕上"),
    ("洋室1", "床", "既存", "フローリング"),
    ("書斎", "壁", "交換", "ビニルクロス"),
)


def _full_pdf(path: Path, *, legend: bool = True, scale: bool = True) -> Path:
    doc = pymupdf.open()
    finish = doc.new_page(width=W, height=H)
    draw_table(
        finish,
        origin=(80.0, 120.0),
        col_widths=(120.0, 90.0, 90.0, 180.0),
        row_height=24.0,
        rows=FINISH_ROWS,
        caption="内装仕上表",
    )
    _plan_page(doc.new_page(width=W, height=H))
    _demolition_page(doc.new_page(width=W, height=H), legend=legend, scale=scale)
    return _save(doc, path)


def _run(pdf: Path, tmp_path: Path, **kwargs) -> app.OnePassResult:
    return app.run(
        pdf,
        case_id="K42-TEST",
        answers_path=tmp_path / "answers.json",
        build_ledger_stage=False,
        **kwargs,
    )


def test_app_emits_both_paths_and_confirms_nothing(tmp_path: Path) -> None:
    result = _run(_full_pdf(tmp_path / "full.pdf"), tmp_path)

    assert result.auto_confirmed_total == 0
    note_rows = [line for line in result.lines if line.path == app.PATH_PLAN_NOTES]
    hatch_rows = [line for line in result.lines if line.path == app.PATH_DEMOLITION_HATCH]
    assert len(note_rows) == 5
    assert result.stages[app.PATH_PLAN_NOTES][app.STAGE_ASSEMBLE] == 5
    assert result.stage_totals()[app.STAGE_ASSEMBLE] == len(result.lines)

    by_text = {(line.work_item, line.place): line for line in note_rows}
    assert by_text[("可動棚6枚", "洋室1")].quantity == 6.0
    assert by_text[("可動棚6枚", "洋室1")].unit == "枚"
    edge = by_text[("床見切新設", "洋室1")]
    assert edge.quantity is None  # 刷られていない数は 0 にしない
    row = edge.as_answer_row(1)
    assert row["色"] == "赤" and row["別"] == "新設/交換"
    assert row["同じ注記の数"]["行"] == 2
    assert row["数量"] is None

    # 面 1 つにつき 2 行、同じ面積
    assert [line.work_item for line in hatch_rows] == ["床組 撤去", "天井組 撤去"]
    assert hatch_rows[0].quantity == hatch_rows[1].quantity
    x0, y0, x1, y1 = BLUE_RECT
    expected = (x1 - x0) * (y1 - y0) * (MM_PER_PT_1_50 / 1000.0) ** 2
    assert hatch_rows[0].quantity == pytest.approx(expected, rel=0.02)
    assert hatch_rows[0].place == "洋室1"
    assert "床組・天井組の撤去範囲" in hatch_rows[0].evidence[0]["根拠"]
    assert result.stages[app.PATH_DEMOLITION_HATCH][app.STAGE_ASSEMBLE] == 2

    near = result.as_dict()["候補(近いが外れ)"]
    assert [m["読んだ文字"] for m in near[app.PATH_PLAN_NOTES]] == ["近い赤"]
    assert not any(line.work_item == "近い赤" for line in result.lines)


def test_app_hatch_without_legend_says_so_and_without_scale_has_no_area(tmp_path: Path) -> None:
    result = _run(_full_pdf(tmp_path / "bare.pdf", legend=False, scale=False), tmp_path)

    assert result.auto_confirmed_total == 0
    hatch_rows = [line for line in result.lines if line.path == app.PATH_DEMOLITION_HATCH]
    assert len(hatch_rows) == 2
    for line in hatch_rows:
        assert line.evidence[0]["根拠"] == "凡例の記載が見当たりません"
        assert line.quantity is None  # 縮尺が無い → 面積を出さない(0 にしない)
    assert result.stages[app.PATH_DEMOLITION_HATCH][app.STAGE_READ] == 0


def test_explicit_pages_override_the_title_search(tmp_path: Path) -> None:
    pdf = _full_pdf(tmp_path / "full.pdf")
    result = _run(pdf, tmp_path, plan_note_pages=[1], demolition_pages=[])

    assert not any(line.path == app.PATH_PLAN_NOTES for line in result.lines)
    assert not any(line.path == app.PATH_DEMOLITION_HATCH for line in result.lines)


def test_cli_accepts_page_lists(tmp_path: Path) -> None:
    import json

    pdf = _full_pdf(tmp_path / "full.pdf")
    out = tmp_path / "out.json"
    code = app.main(
        [str(pdf), "--case-id", "K42", "--out", str(out), "--no-ledger",
         "--plan-note-pages", "2", "--demolition-pages", "3"]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["自動確定"]["合計"] == 0
    assert {r["道"] for r in payload["工事項目"]} >= {app.PATH_PLAN_NOTES, app.PATH_DEMOLITION_HATCH}
