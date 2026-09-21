"""`axes/image_axis/pdf_pages.py` の回帰テスト。

**このモジュールで守りたいのは精度ではなく安全側の既定値である。**
縮尺が分からないまま紙のスケールを実寸として返してしまうと、1/50 の図面では
50 倍ずれた長さが「もっともらしい数値」として下流に入る(v8 8章の穴2)。
そこを固定するテストを中心に置いた。

テスト用の PDF はその場で組み立てるので、実案件の図面ファイルは要らない。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from axes.image_axis.pdf_pages import MM_PER_POINT, describe, rasterize


def _vector_pdf(path: Path, pages: int = 2, with_text: bool = True) -> Path:
    """線と文字がベクターで入った PDF(= CAD 出力に相当)を作る。"""
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)  # A4 ポイント
        page.draw_line(pymupdf.Point(50, 50), pymupdf.Point(500, 50))
        page.draw_rect(pymupdf.Rect(60, 100, 400, 300))
        if with_text:
            page.insert_text(pymupdf.Point(60, 400), f"SCALE 1/50 PAGE {index}")
    doc.save(path)
    doc.close()
    return path


def _scanned_pdf(path: Path) -> Path:
    """紙をスキャンした画像が 1 枚貼ってあるだけの PDF を作る。"""
    image = np.full((200, 300), 255, np.uint8)
    image[80:120, 40:260] = 0
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, 300, 200, image.tobytes(), False)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


# ---------------------------------------------------------------------------
# 縮尺(いちばん大事なところ)
# ---------------------------------------------------------------------------


def test_drawing_scale_is_unknown_unless_given(tmp_path: Path) -> None:
    """縮尺を渡さない限り実寸は None。**0 でも 1 でも紙のスケールでもない。**"""
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=100)[0]
    assert page.drawing_mm_per_pixel() is None


def test_paper_scale_is_not_reused_as_drawing_scale(tmp_path: Path) -> None:
    """紙のスケールと実寸のスケールは、縮尺の分だけ必ず食い違う。

    ここが一致してしまう実装だと、1/50 の図面で 50 倍ずれた長さが通る。
    """
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=100)[0]
    paper = page.mm_per_pixel
    drawing = page.drawing_mm_per_pixel(scale_denominator=50.0)
    assert drawing is not None
    assert math.isclose(drawing, paper * 50.0)
    assert not math.isclose(drawing, paper)


def test_zero_or_negative_scale_is_rejected(tmp_path: Path) -> None:
    """0 や負の縮尺を黙って受けると、長さが 0 や負になって下流で気づけない。"""
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=100)[0]
    for bad in (0.0, -1.0, -50.0):
        with pytest.raises(ValueError):
            page.drawing_mm_per_pixel(scale_denominator=bad)


# ---------------------------------------------------------------------------
# ページの中身の判定
# ---------------------------------------------------------------------------


def test_vector_page_is_reported_as_vector(tmp_path: Path) -> None:
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=72)[0]
    assert page.content_kind == "vector"
    assert page.vector_draw_count > 0
    assert page.embedded_image_count == 0


def test_scanned_page_is_reported_as_raster_with_no_text(tmp_path: Path) -> None:
    """スキャン図面は「ラスター1枚・文字0」として出る。

    実案件 P011 の図面 34 ページがちょうどこの形だった。文章軸が読む材料が
    1 文字も無いことを、ここで取り違えないようにする。
    """
    page = rasterize(_scanned_pdf(tmp_path / "s.pdf"), dpi=72)[0]
    assert page.content_kind == "raster"
    assert page.vector_draw_count == 0
    assert page.embedded_image_count == 1
    assert page.text_span_count == 0


def test_embedded_text_is_counted_when_present(tmp_path: Path) -> None:
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1, with_text=True), dpi=72)[0]
    assert page.text_span_count > 0
    assert "SCALE" in page.text


def test_whitespace_only_text_counts_as_zero_spans(tmp_path: Path) -> None:
    """空白だけの断片を数えると、スキャン図面を「文字がある」と誤って分類する。

    `get_text()` は文字を置いていないページでも改行を返すことがあるので、
    生の文字数ではなく **空白を除いた断片の数**で判定しないといけない。
    """
    path = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    page_obj = doc.new_page(width=595, height=842)
    page_obj.draw_line(pymupdf.Point(50, 50), pymupdf.Point(500, 50))
    page_obj.insert_text(pymupdf.Point(60, 400), "    ")
    doc.save(path)
    doc.close()

    page = rasterize(path, dpi=72)[0]
    assert page.text != ""  # 生のテキストは空白と改行を含む
    assert page.text.strip() == ""
    assert page.text_span_count == 0


# ---------------------------------------------------------------------------
# ラスター化
# ---------------------------------------------------------------------------


def test_resolution_scales_with_dpi(tmp_path: Path) -> None:
    path = _vector_pdf(tmp_path / "a.pdf", pages=1)
    low = rasterize(path, dpi=72)[0]
    high = rasterize(path, dpi=144)[0]
    assert high.image.shape[0] == pytest.approx(low.image.shape[0] * 2, abs=2)
    assert math.isclose(high.mm_per_pixel, low.mm_per_pixel / 2, rel_tol=1e-9)


def test_paper_size_is_reported_in_millimetres(tmp_path: Path) -> None:
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=72)[0]
    assert page.paper_width_mm == pytest.approx(595 * MM_PER_POINT, abs=0.5)
    assert page.paper_height_mm == pytest.approx(842 * MM_PER_POINT, abs=0.5)


def test_image_is_grayscale_and_not_binarized(tmp_path: Path) -> None:
    """2 値化は下流(大津の二値化)の仕事。ここで潰すと前処理を差し替えられない。"""
    page = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=1), dpi=72)[0]
    assert page.image.ndim == 2
    assert page.image.dtype == np.uint8


def test_page_selection(tmp_path: Path) -> None:
    path = _vector_pdf(tmp_path / "a.pdf", pages=3)
    pages = rasterize(path, dpi=72, pages=range(1, 3))
    assert [p.page_index for p in pages] == [1, 2]


def test_out_of_range_page_raises(tmp_path: Path) -> None:
    path = _vector_pdf(tmp_path / "a.pdf", pages=2)
    with pytest.raises(IndexError):
        rasterize(path, dpi=72, pages=range(5, 6))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rasterize(tmp_path / "nope.pdf")


def test_invalid_dpi_raises(tmp_path: Path) -> None:
    path = _vector_pdf(tmp_path / "a.pdf", pages=1)
    for bad in (0, -100):
        with pytest.raises(ValueError):
            rasterize(path, dpi=bad)


def test_describe_lists_every_page(tmp_path: Path) -> None:
    pages = rasterize(_vector_pdf(tmp_path / "a.pdf", pages=3), dpi=72)
    table = describe(pages)
    assert table.count("\n") == 4  # 見出し + 区切り + 3 行
    for number in ("| 1 ", "| 2 ", "| 3 "):
        assert number in table
