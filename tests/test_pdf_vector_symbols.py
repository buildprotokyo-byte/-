"""`axes/image_axis/pdf_vector_symbols.py` の回帰テスト。

守りたいのは 2 つ。

1. **取り出せないときに推測しないこと。** 縮尺が読めない PDF(スキャンした
   だけの図面)に対して `extract_scale()` は None を返さなければならない。
   ここで紙のスケールを返してしまうと、1/50 の図面で 50 倍ずれた長さが
   下流に入る(v8 8章の穴2)。
2. **円弧の幾何で絞り込めていること。** 円弧かどうかだけで拾うと、
   図面上の丸や曲線を建具として数えてしまう。判定は半径と中心角で行い、
   **回転に依存しない**(斜めに付く建具を落とさない)。

テスト用の PDF はその場で組み立てる。
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_vector_symbols import (
    _SCALE_RE,
    DrawingScale,
    extract_scale,
    find_door_arcs,
)

#: 1mm(実寸)が 1/50 の図面で何ポイントになるか。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72


def _draw_quarter_arc(page: pymupdf.Page, x: float, y: float, radius_pt: float) -> None:
    """(x, y) を中心とする四分円を、半径 2 本とあわせて扇形として描く。

    実図面ではこの描き方の建具もある(``get_drawings()`` の items が ``lcc``)。
    """
    start = pymupdf.Point(x + radius_pt, y)
    shape = page.new_shape()
    shape.draw_sector(pymupdf.Point(x, y), start, 90)
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _draw_arc_only(
    page: pymupdf.Page, x: float, y: float, radius_pt: float, start_deg: float
) -> None:
    """半径の線を描かず、**円弧だけ**を 2 本のベジェ曲線で描く。

    実図面ではこの描き方の建具があり(``get_drawings()`` の items が ``cc``)、
    斜めに振れているとき外接矩形が細長くなる。
    """
    shape = page.new_shape()
    for segment in range(2):  # 90 度を 45 度ずつ 2 本のベジェで描く(実図面と同じ)
        a0 = math.radians(start_deg + segment * 45.0)
        a1 = math.radians(start_deg + (segment + 1) * 45.0)
        p0 = (x + radius_pt * math.cos(a0), y + radius_pt * math.sin(a0))
        p3 = (x + radius_pt * math.cos(a1), y + radius_pt * math.sin(a1))
        # 円弧をベジェで近似するときの制御点の長さ。
        handle = 4.0 / 3.0 * math.tan((a1 - a0) / 4.0) * radius_pt
        p1 = (p0[0] - handle * math.sin(a0), p0[1] + handle * math.cos(a0))
        p2 = (p3[0] + handle * math.sin(a1), p3[1] - handle * math.cos(a1))
        shape.draw_bezier(
            pymupdf.Point(*p0), pymupdf.Point(*p1), pymupdf.Point(*p2), pymupdf.Point(*p3)
        )
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _plan_pdf(
    path: Path,
    scale_text: str = "1/50（A3）",
    door_widths_mm: tuple[float, ...] = (800.0,),
    extra_circle_mm: float | None = None,
) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)  # A3 横
    page.insert_text(pymupdf.Point(900, 800), f"縮尺 {scale_text}")
    x = 100.0
    for width_mm in door_widths_mm:
        _draw_quarter_arc(page, x, 300.0, width_mm * PT_PER_MM_AT_50)
        x += 200.0
    if extra_circle_mm is not None:
        # 家具などの「円」。建具ではない。
        r = extra_circle_mm * PT_PER_MM_AT_50 / 2
        shape = page.new_shape()
        shape.draw_circle(pymupdf.Point(700, 600), r)
        shape.finish(color=(0, 0, 0), width=0.3)
        shape.commit()
    doc.save(path)
    doc.close()
    return path


def _scanned_pdf(path: Path) -> Path:
    """線も文字も図形データとして持たない PDF(スキャン相当)。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, 10, 10, bytes([255] * 100), False)
    page.insert_image(pymupdf.Rect(0, 0, 1190, 842), pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


# ---------------------------------------------------------------------------
# 縮尺
# ---------------------------------------------------------------------------


def test_scale_is_read_from_the_title_block(tmp_path: Path) -> None:
    scale = extract_scale(_plan_pdf(tmp_path / "a.pdf"), 0)
    assert scale is not None
    assert scale.denominator == 50.0


@pytest.mark.parametrize(
    "text,expected",
    [("1/50（A3）", 50.0), ("1:100", 100.0), ("1／30 (A1)", 30.0), ("1：20", 20.0), ("1 / 5", 5.0)],
)
def test_scale_notation_variants(text: str, expected: float) -> None:
    """表題欄は全角のスラッシュやコロンが混ざる。

    PDF に全角記号を埋め込むには対応フォントが要り、ここで作る試験用 PDF の
    既定フォントでは化けてしまう。測りたいのは表記のゆれを吸収できるか
    なので、正規表現そのものに当てる。
    """
    match = _SCALE_RE.search(text)
    assert match is not None
    assert float(match.group(1)) == expected


def test_scale_is_none_when_the_page_has_no_text(tmp_path: Path) -> None:
    """**いちばん大事なテスト。** スキャン図面では縮尺が読めない。

    ここで紙のスケールを返す実装にすると、1/50 の図面で 50 倍ずれた長さが
    「もっともらしい数値」として通ってしまう。
    """
    assert extract_scale(_scanned_pdf(tmp_path / "s.pdf"), 0) is None


def test_scale_converts_to_real_millimetres(tmp_path: Path) -> None:
    scale = extract_scale(_plan_pdf(tmp_path / "a.pdf"), 0)
    assert scale is not None
    # 200dpi なら紙の 1px = 0.127mm、実寸は its 50 倍。
    assert math.isclose(scale.mm_per_pixel(200), 25.4 / 200 * 50, rel_tol=1e-9)
    assert math.isclose(scale.mm_per_point, 25.4 / 72 * 50, rel_tol=1e-9)


def test_invalid_dpi_raises(tmp_path: Path) -> None:
    scale = extract_scale(_plan_pdf(tmp_path / "a.pdf"), 0)
    assert scale is not None
    with pytest.raises(ValueError):
        scale.mm_per_pixel(0)


# ---------------------------------------------------------------------------
# 建具記号
# ---------------------------------------------------------------------------


def test_door_arcs_are_found(tmp_path: Path) -> None:
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(800.0, 700.0, 900.0))
    scale = extract_scale(path, 0)
    assert scale is not None
    arcs = find_door_arcs(path, 0, scale)
    assert len(arcs) == 3
    widths = sorted(round(a.width_mm) for a in arcs)
    assert widths == pytest.approx([700, 800, 900], abs=15)


def test_arcs_outside_the_real_size_range_are_rejected(tmp_path: Path) -> None:
    """建具として成り立たない大きさの円弧は拾わない。

    実寸(半径)で絞り込まないと、図面の枠線の角丸や小さな曲線まで建具になる。
    """
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(80.0, 3000.0))
    scale = extract_scale(path, 0)
    assert scale is not None
    assert find_door_arcs(path, 0, scale) == []


def test_a_full_circle_is_not_a_door(tmp_path: Path) -> None:
    """家具の円(ダイニングテーブルなど)を建具として数えない。

    実図面ではこの種の円が各室にあるので、ここが効かないと個数が膨らむ。
    """
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(800.0,), extra_circle_mm=1200.0)
    scale = extract_scale(path, 0)
    assert scale is not None
    arcs = find_door_arcs(path, 0, scale)
    # 1200mm の円は大きさも縦横比も建具と区別できない。中心角(360 度)で落とす。
    assert len(arcs) == 1
    assert arcs[0].width_mm == pytest.approx(800, abs=15)


def test_results_are_ordered_and_stable(tmp_path: Path) -> None:
    """呼び出すたびに順番が変わると、下流の突き合わせが不安定になる。

    2 回呼んで一致するだけでは足りない(PDF の読み出し順が偶然そろうため)。
    **図面上の位置の順に並んでいること**まで見る。
    """
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(900.0, 700.0, 800.0))
    scale = extract_scale(path, 0)
    assert scale is not None
    arcs = find_door_arcs(path, 0, scale)
    assert len(arcs) == 3
    keys = [(round(a.rect_pt[1], 1), round(a.rect_pt[0], 1)) for a in arcs]
    assert keys == sorted(keys)
    assert keys != sorted(keys, reverse=True)  # 3 件が同じ位置に潰れていないこと
    assert [a.rect_pt for a in find_door_arcs(path, 0, scale)] == [a.rect_pt for a in arcs]


def test_rect_px_scales_with_dpi(tmp_path: Path) -> None:
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(800.0,))
    scale = extract_scale(path, 0)
    assert scale is not None
    arc = find_door_arcs(path, 0, scale)[0]
    low, high = arc.rect_px(72), arc.rect_px(144)
    assert high[0] == pytest.approx(low[0] * 2, abs=2)


def test_a_door_drawn_at_an_angle_is_still_found(tmp_path: Path) -> None:
    """**回帰テスト。** 斜めに振れる建具を落とさない。

    外接矩形の縦横比で絞っていた最初の実装は、45 度に付く建具を
    「細長いから建具ではない」として落としていた(実図面で 15 件中 2 件)。
    円弧そのものの半径と中心角で見ればこれは起きない。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    _draw_arc_only(page, 400.0, 400.0, 800.0 * PT_PER_MM_AT_50, start_deg=135.0)
    path = tmp_path / "斜め.pdf"
    doc.save(path)
    doc.close()
    scale = extract_scale(path, 0)
    assert scale is not None
    arcs = find_door_arcs(path, 0, scale)
    assert len(arcs) == 1
    assert arcs[0].width_mm == pytest.approx(800, abs=15)
    # 外接矩形は正方形から程遠い。ここで落としてはいけない。
    x0, y0, x1, y1 = arcs[0].rect_pt
    assert max(x1 - x0, y1 - y0) / min(x1 - x0, y1 - y0) > 1.4


def test_swept_angle_is_reported(tmp_path: Path) -> None:
    """判定の根拠(何度ぶんなぞったか)を呼び出し側から見えるようにしておく。"""
    path = _plan_pdf(tmp_path / "a.pdf", door_widths_mm=(800.0,))
    scale = extract_scale(path, 0)
    assert scale is not None
    assert find_door_arcs(path, 0, scale)[0].swept_degrees == pytest.approx(90, abs=6)


def test_a_semicircle_is_not_a_door(tmp_path: Path) -> None:
    """半円(便器・洗面ボウルなど)は開き戸の振りではない。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    shape = page.new_shape()
    radius = 800.0 * PT_PER_MM_AT_50
    shape.draw_sector(pymupdf.Point(300, 300), pymupdf.Point(300 + radius, 300), 180)
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    path = tmp_path / "半円.pdf"
    doc.save(path)
    doc.close()
    scale = extract_scale(path, 0)
    assert scale is not None
    assert find_door_arcs(path, 0, scale) == []


def test_a_non_circular_curve_is_not_a_door(tmp_path: Path) -> None:
    """**検出力テストで見つかった穴。** 円でない曲線を落とせているか。

    大きさも中心角も建具と同じだが円ではない曲線(楕円の一部、家具の曲線、
    床張方向の網掛け)は実図面に多い。円への当てはまりを見ていないと、
    これが全部建具として数えられる。

    最初はこのテストが**通ってしまっていた**。許容誤差 3% では 4 割まで
    潰した楕円(残差 2.5%)も通ってしまうため、判定が事実上効いていなかった。
    実図面の本物の建具の残差(最大 0.033%)を測って 0.5% に締め直した。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    a = 800.0 * PT_PER_MM_AT_50
    b = a * 0.4  # 短径を長径の 4 割まで潰した四分楕円
    k = 0.5523
    shape = page.new_shape()
    shape.draw_bezier(
        pymupdf.Point(400 + a, 400),
        pymupdf.Point(400 + a, 400 - b * k),
        pymupdf.Point(400 + a * k, 400 - b),
        pymupdf.Point(400, 400 - b),
    )
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()
    path = tmp_path / "楕円.pdf"
    doc.save(path)
    doc.close()
    scale = extract_scale(path, 0)
    assert scale is not None
    assert find_door_arcs(path, 0, scale) == []


def test_bad_arguments_raise(tmp_path: Path) -> None:
    path = _plan_pdf(tmp_path / "a.pdf")
    scale = DrawingScale(denominator=50.0, source_text="1/50")
    with pytest.raises(ValueError):
        find_door_arcs(path, 0, scale, min_mm=0)
    with pytest.raises(ValueError):
        find_door_arcs(path, 0, scale, min_mm=900, max_mm=400)
    with pytest.raises(ValueError):
        find_door_arcs(path, 0, scale, min_degrees=120, max_degrees=60)
    with pytest.raises(ValueError):
        find_door_arcs(path, 0, scale, min_degrees=0)
    with pytest.raises(IndexError):
        find_door_arcs(path, 9, scale)
