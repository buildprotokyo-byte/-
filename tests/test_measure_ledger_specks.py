"""K-26: 捨てた点をつなぐ数え方(`benchmarks/measure_ledger_specks.joined_shapes`)。合成の図面だけで確かめる。"""

import math

import pymupdf

from axes.image_axis.candidate_ledger import LedgerSettings
from benchmarks.measure_ledger_specks import joined_shapes, shape_kind


def _page_with_tiny_circle(segments: int = 120, radius: float = 6.0):
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    points = [
        pymupdf.Point(300 + radius * math.cos(2 * math.pi * k / segments), 300 + radius * math.sin(2 * math.pi * k / segments))
        for k in range(segments + 1)
    ]
    # 円を細かい直線に割り、1 本ずつ別のパスとして描く(凡例の記号と同じ描き方)。
    for a, b in zip(points, points[1:]):
        page.draw_line(a, b, width=0.3)
    # 離れたところに、大きさ 0 の点を 1 つ。
    page.draw_line(pymupdf.Point(600, 600), pymupdf.Point(600, 600), width=0.5)
    return doc, page


def test_tiny_segments_of_a_circle_join_into_one_shape():
    doc, page = _page_with_tiny_circle()
    counts, shapes = joined_shapes(page, LedgerSettings())
    assert counts["つなぐと区切り以上の形になる"] == 120
    assert len(shapes) == 1
    box = shapes[0]
    # 直径 12pt = 横 約 10 単位。小輪郭の大きさに収まる。
    assert 5 < box[2] - box[0] < 20
    doc.close()


def test_isolated_zero_point_stays_alone():
    doc, page = _page_with_tiny_circle()
    counts, _ = joined_shapes(page, LedgerSettings())
    assert counts["ひとつだけ(大きさ0の点)"] == 1
    doc.close()


def test_shape_kind_of_zero_length_line():
    doc, page = _page_with_tiny_circle(segments=8)
    kinds = {shape_kind(d) for d in page.get_drawings()}
    assert "大きさ0の点" in kinds
    doc.close()
