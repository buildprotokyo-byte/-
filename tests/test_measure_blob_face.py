"""周18 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from benchmarks.measure_blob_face import gather, measure_page, top_face

SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
FAR = ((100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0))


def test_いちばん多い面を返す() -> None:
    assert top_face({0: 3, 1: 1}) == 0


def test_同数なら1つに決まらない() -> None:
    """**そのページは数えない**(本物も囮も同じ扱い)。"""
    assert top_face({0: 2, 1: 2}) is None
    assert top_face({}) is None


def test_別々の名前を数える() -> None:
    points = [("洋室", (5.0, 5.0)), ("洋室", (6.0, 6.0)), ("便所", (7.0, 7.0))]
    counts, names = gather(points, [SQUARE, FAR], 1.0)
    assert counts[0] == 3
    assert names[0] == {"洋室", "便所"}


def test_届かない点は数えない() -> None:
    counts, names = gather([("洋室", (500.0, 500.0))], [SQUARE], 1.0)
    assert counts == {} and names == {}


def _plan(path: Path, *, same_face: bool) -> Path:
    """**合成の紙**: 室名と天井高を、同じ面に置くか別の面に置くか。"""
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    shape.draw_line(pymupdf.Point(300, 50), pymupdf.Point(300, 350))
    for y in (120, 190, 270):
        shape.draw_line(pymupdf.Point(50, y), pymupdf.Point(300, y))
    shape.finish(width=1)
    shape.commit()
    page.insert_text((100, 160), "洋室", fontsize=9, fontname="japan")
    page.insert_text((120, 175), "便所", fontsize=9, fontname="japan")
    page.insert_text((150, 160) if same_face else (360, 130), "CH=2400", fontsize=9)
    document.save(path)
    document.close()
    return path


def test_同じ面に置けば同じ面と出る(tmp_path: Path) -> None:
    got = measure_page(
        _plan(tmp_path / "same.pdf", same_face=True), 0, ["洋室", "便所"], 20260925
    )
    assert got["線1_同じ面"] is True
    assert got["線2_別々の室名"] == 2


def test_別の面に置けば別の面と出る(tmp_path: Path) -> None:
    """**この紙で真になるなら、数え方が向きを取り違えている。**"""
    got = measure_page(
        _plan(tmp_path / "other.pdf", same_face=False), 0, ["洋室", "便所"], 20260925
    )
    assert got["線1_同じ面"] is False


def test_天井高の注記が無いページは平面図とみなさない(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "洋室", fontsize=9, fontname="japan")
    document.save(path)
    document.close()
    assert measure_page(path, 0, ["洋室"], 20260925) == {
        "ページ": 1,
        "平面図とみなす": False,
    }


def test_仮の辺の割合が出る(tmp_path: Path) -> None:
    """**守りの線が数字として出ていること。**"""
    got = measure_page(
        _plan(tmp_path / "same.pdf", same_face=True), 0, ["洋室", "便所"], 20260925
    )
    face = got["室名が集まる面"]
    assert face["辺の数"] > 0
    assert 0.0 <= face["線3_仮の辺の割合"] <= 1.0


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    path = _plan(tmp_path / "same.pdf", same_face=True)
    first = measure_page(path, 0, ["洋室", "便所"], 20260925)
    assert first == measure_page(path, 0, ["洋室", "便所"], 20260925)
