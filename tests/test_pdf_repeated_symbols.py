"""`axes/image_axis/pdf_repeated_symbols.py` の回帰テスト。

守りたいのは 4 つ。

1. **同じ形が何度も出てくることを、テンプレートを持たずに見つけられること。**
   ラスターのテンプレート照合は的中 0 件だった(`docs/real_drawing_eval_report.md`
   4 節・11 節)。原因の 1 つは「探すべき形の出どころが無い」ことだった。
   CAD 由来の PDF では、記号は**同じ図形の複製**として入っているので、
   テンプレートを用意せずに「繰り返し」そのものを手がかりにできる。
2. **回転・鏡像で同じものを別々に数えないこと。** コンセントやスイッチは
   壁の向きに合わせて回して置かれる。回すたびに別の種類として数えたら
   個数は当てにならない。
3. **名前を作らないこと。** 繰り返しが見つかっても、それが何の記号かは
   図形からは分からない。凡例と突き合わせて初めて名前が付く。
   突き合わない群は **名前なしのまま**返す(勝手に名付けない)。
4. **0 件を「無い」と言わないこと。** 大きさの窓から外れた記号、1 回しか
   出てこない記号は原理的に落ちる。限界は出力に残す。

テスト用の PDF はその場で組み立てる(実図面はコミットしない)。
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf
import pytest

from axes.image_axis.pdf_repeated_symbols import (
    METHOD_LEGEND_SYMBOL,
    METHOD_REPEATED_SYMBOL,
    LegendSymbol,
    SymbolCluster,
    find_repeated_symbols,
    name_clusters,
    read_legend_symbols,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale

#: 1mm(実寸)が 1/50 の図面で何ポイントになるか。
PT_PER_MM_AT_50 = (1 / 50) / 25.4 * 72

SCALE_50 = DrawingScale(denominator=50.0, source_text="1/50")


def _draw_outlet(page: pymupdf.Page, x: float, y: float, angle_deg: float, size_pt: float) -> None:
    """コンセントらしい記号: 半円 + 引出線 2 本。``angle_deg`` だけ回して描く。

    実図面の記号は壁の向きに合わせて回して置かれるので、テストでも回す。
    """
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def place(dx: float, dy: float) -> pymupdf.Point:
        return pymupdf.Point(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)

    shape = page.new_shape()
    # 半円(2 本のベジェで描く)
    radius = size_pt / 2.0
    for segment in range(2):
        a0 = math.radians(segment * 90.0)
        a1 = math.radians((segment + 1) * 90.0)
        handle = 4.0 / 3.0 * math.tan((a1 - a0) / 4.0) * radius
        p0 = (radius * math.cos(a0), radius * math.sin(a0))
        p3 = (radius * math.cos(a1), radius * math.sin(a1))
        p1 = (p0[0] - handle * math.sin(a0), p0[1] + handle * math.cos(a0))
        p2 = (p3[0] + handle * math.sin(a1), p3[1] - handle * math.cos(a1))
        shape.draw_bezier(place(*p0), place(*p1), place(*p2), place(*p3))
    # 引出線 2 本
    shape.draw_line(place(-radius, 0.0), place(-radius - size_pt, 0.0))
    shape.draw_line(place(0.0, radius), place(0.0, radius + size_pt * 0.4))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _draw_switch(page: pymupdf.Page, x: float, y: float, angle_deg: float, size_pt: float) -> None:
    """スイッチらしい記号: 小さな丸 + 斜めの線。コンセントとは別の形。"""
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def place(dx: float, dy: float) -> pymupdf.Point:
        return pymupdf.Point(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)

    shape = page.new_shape()
    radius = size_pt / 3.0
    for segment in range(4):
        a0 = math.radians(segment * 90.0)
        a1 = math.radians((segment + 1) * 90.0)
        handle = 4.0 / 3.0 * math.tan((a1 - a0) / 4.0) * radius
        p0 = (radius * math.cos(a0), radius * math.sin(a0))
        p3 = (radius * math.cos(a1), radius * math.sin(a1))
        p1 = (p0[0] - handle * math.sin(a0), p0[1] + handle * math.cos(a0))
        p2 = (p3[0] + handle * math.sin(a1), p3[1] - handle * math.cos(a1))
        shape.draw_bezier(place(*p0), place(*p1), place(*p2), place(*p3))
    shape.draw_line(place(radius, 0.0), place(radius + size_pt, size_pt * 0.8))
    shape.finish(color=(0, 0, 0), width=0.3)
    shape.commit()


def _plan_pdf(
    path: Path,
    outlets: tuple[tuple[float, float, float], ...] = (),
    switches: tuple[tuple[float, float, float], ...] = (),
    with_big_frame: bool = True,
) -> Path:
    """電気位置図らしいページを 1 枚作る。``(x, y, 回転角)`` の並びで記号を置く。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)  # A3 横
    page.insert_text(pymupdf.Point(900, 800), "縮尺 1/50")
    if with_big_frame:
        # 図面の外枠と、その内側の同じ大きさの枠。**合同なので 2 個の群になる。**
        # 繰り返しているのに記号ではない、という場合を作ってある
        # (これを落とせるのは「繰り返し」ではなく「大きさの窓」だけ)。
        for offset in (0.0, 6.0):
            shape = page.new_shape()
            shape.draw_rect(pymupdf.Rect(20 + offset, 20 + offset, 1150 + offset, 802 + offset))
            shape.finish(color=(0, 0, 0), width=0.5)
            shape.commit()
    size_pt = 200.0 * PT_PER_MM_AT_50  # 実寸 200mm 相当
    for x, y, angle in outlets:
        _draw_outlet(page, x, y, angle, size_pt)
    for x, y, angle in switches:
        _draw_switch(page, x, y, angle, size_pt)
    doc.save(path)
    doc.close()
    return path


def test_繰り返す記号をテンプレート無しで群にまとめて数える(tmp_path: Path) -> None:
    pdf = _plan_pdf(
        tmp_path / "plan.pdf",
        outlets=((200, 200, 0.0), (400, 200, 0.0), (600, 200, 0.0)),
        switches=((200, 500, 0.0), (400, 500, 0.0)),
    )
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)

    counts = sorted(c.count for c in clusters)
    assert counts == [2, 3], f"3 個の群と 2 個の群が出るはず: {counts}"
    # 位置は全件残る(どこにあったかを言えない数量は根拠にならない)。
    for cluster in clusters:
        assert len(cluster.positions_pt) == cluster.count
    assert all(c.method_id == METHOD_REPEATED_SYMBOL for c in clusters)


def test_回転して置かれた同じ記号を同じ群として数える(tmp_path: Path) -> None:
    pdf = _plan_pdf(
        tmp_path / "rotated.pdf",
        outlets=((200, 200, 0.0), (400, 200, 90.0), (600, 200, 180.0), (800, 200, 37.0)),
    )
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    assert len(clusters) == 1, f"回転しただけで別の群になっている: {clusters}"
    assert clusters[0].count == 4


def test_別の形を同じ群にまとめない(tmp_path: Path) -> None:
    pdf = _plan_pdf(
        tmp_path / "mixed.pdf",
        outlets=((200, 200, 0.0), (400, 200, 0.0)),
        switches=((200, 500, 0.0), (400, 500, 0.0)),
    )
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    assert len(clusters) == 2, f"別の形が 1 つの群になっている: {clusters}"


def test_大きさの窓から外れたものは記号として数えない(tmp_path: Path) -> None:
    """図面の外枠のような大きな図形は、繰り返していなくても記号ではない。"""
    pdf = _plan_pdf(
        tmp_path / "frame.pdf",
        outlets=((200, 200, 0.0), (400, 200, 0.0)),
        with_big_frame=True,
    )
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    assert len(clusters) == 1, f"外枠が記号として数えられている: {clusters}"
    assert clusters[0].count == 2
    assert clusters[0].size_mm <= 1500.0


def test_1回しか出てこない形は落ちる_その限界を出力に残す(tmp_path: Path) -> None:
    pdf = _plan_pdf(tmp_path / "single.pdf", outlets=((200, 200, 0.0),))
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    assert clusters == [], "1 個だけの形は既定では群にならない"
    # min_count を 1 にすれば拾える(落ちているのは方針であって、能力ではない)。
    clusters_all = find_repeated_symbols(pdf, 0, SCALE_50, min_count=1)
    assert len(clusters_all) == 1


def _legend_pdf(path: Path) -> Path:
    """凡例のページ: 記号の絵と、その左に名前が書いてある。"""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    size_pt = 200.0 * PT_PER_MM_AT_50
    page.insert_text(pymupdf.Point(80, 205), "埋込コンセント", fontname="japan")
    _draw_outlet(page, 400, 200, 0.0, size_pt)
    page.insert_text(pymupdf.Point(80, 305), "片切スイッチ", fontname="japan")
    _draw_switch(page, 400, 300, 0.0, size_pt)
    doc.save(path)
    doc.close()
    return path


def test_凡例の記号に名前が付く(tmp_path: Path) -> None:
    legend = read_legend_symbols(_legend_pdf(tmp_path / "legend.pdf"), 0, SCALE_50)
    names = sorted(s.name for s in legend)
    assert names == ["埋込コンセント", "片切スイッチ"], names
    assert all(s.method_id == METHOD_LEGEND_SYMBOL for s in legend)


def test_凡例と突き合わせて群に名前が付く_突き合わない群は名前なしのまま(tmp_path: Path) -> None:
    pdf = _plan_pdf(
        tmp_path / "plan2.pdf",
        outlets=((200, 200, 0.0), (400, 200, 90.0), (600, 200, 0.0)),
        switches=((200, 500, 0.0), (400, 500, 0.0)),
    )
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    legend = read_legend_symbols(_legend_pdf(tmp_path / "legend2.pdf"), 0, SCALE_50)
    named = name_clusters(clusters, legend)

    by_name = {n.name: n.count for n in named}
    assert by_name == {"埋込コンセント": 3, "片切スイッチ": 2}, by_name


def test_凡例に無い形は名前を作らない(tmp_path: Path) -> None:
    pdf = _plan_pdf(tmp_path / "plan3.pdf", switches=((200, 500, 0.0), (400, 500, 0.0)))
    clusters = find_repeated_symbols(pdf, 0, SCALE_50)
    # 凡例にコンセントしか載っていない
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(80, 205), "埋込コンセント", fontname="japan")
    _draw_outlet(page, 400, 200, 0.0, 200.0 * PT_PER_MM_AT_50)
    legend_path = tmp_path / "legend3.pdf"
    doc.save(legend_path)
    doc.close()

    legend = read_legend_symbols(legend_path, 0, SCALE_50)
    named = name_clusters(clusters, legend)
    assert len(named) == 1
    assert named[0].name is None, "凡例に無い形に名前を作ってはいけない"
    assert named[0].count == 2


def test_スキャンのページでは0件になる_それは記号が無いことではない(tmp_path: Path) -> None:
    """文字も図形も入っていないページ。0 件だが「記号が無い」ではない。"""
    doc = pymupdf.open()
    doc.new_page(width=1190, height=842)
    path = tmp_path / "blank.pdf"
    doc.save(path)
    doc.close()
    assert find_repeated_symbols(path, 0, SCALE_50) == []


def test_ページ番号が範囲外なら例外(tmp_path: Path) -> None:
    pdf = _plan_pdf(tmp_path / "plan4.pdf", outlets=((200, 200, 0.0), (400, 200, 0.0)))
    with pytest.raises(IndexError):
        find_repeated_symbols(pdf, 5, SCALE_50)
