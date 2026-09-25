"""周20 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks.measure_simple_faces import measure_page, nearest, nested, spread

SMALL = ((2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0))
BIG = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
AWAY = ((20.0, 20.0), (30.0, 20.0), (30.0, 30.0), (20.0, 30.0))


def test_まるごと入っていれば入れ子() -> None:
    assert nested(SMALL, BIG)


def test_はみ出していれば入れ子ではない() -> None:
    straddle = ((8.0, 8.0), (12.0, 8.0), (12.0, 12.0), (8.0, 12.0))
    assert not nested(straddle, BIG)
    assert not nested(AWAY, BIG)


def test_いちばん近い輪郭までの距離() -> None:
    """**角がいちばん近いときは角までの距離**(辺までの垂線ではない)。"""
    assert nearest((5.0, 5.0), [SMALL, AWAY]) == pytest.approx(2.0**0.5)
    assert nearest((5.0, 3.0), [SMALL, AWAY]) == pytest.approx(1.0)
    assert nearest((5.0, 5.0), []) is None


def test_ばらつきは中央値以外も出す() -> None:
    """**中央値は 17 個だと 1 個で動く**ので、最小・最大・四分位も出す。"""
    got = spread([1.0, 2.0, 3.0, 4.0])
    assert got["中央値"] == 2.5 and got["最小"] == 1.0 and got["最大"] == 4.0
    assert spread([]) == {"件数": 0}


def _plan(path: Path) -> Path:
    """**合成の紙**: 四角い室に天井高を刷る。"""
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    shape.draw_line(pymupdf.Point(300, 50), pymupdf.Point(300, 350))
    for y in (120, 190, 270):
        shape.draw_line(pymupdf.Point(50, y), pymupdf.Point(300, y))
    shape.finish(width=1)
    shape.commit()
    page.insert_text((150, 160), "CH=2400", fontsize=9)
    document.save(path)
    document.close()
    return path


def test_合成の紙では面積と距離が出る(tmp_path: Path) -> None:
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, 20260925)
    assert got["面積㎡"]
    assert got["天井高からの距離mm"]
    assert got["囮の距離mm"]


def test_辺の少ない紙では入れ子が0(tmp_path: Path) -> None:
    """**化け物の面が無い紙では、入れ子は起きない。**"""
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, 20260925)
    assert got["辺が51本以上の面"] == 0
    assert got["線2_化け物の内側に入れ子"] == 0


def test_天井高の注記が無いページは平面図とみなさない(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, 180, 180))
    shape.finish(width=1)
    shape.commit()
    document.save(path)
    document.close()
    assert measure_page(path, 0, 20260925) == {"ページ": 1, "平面図とみなす": False}


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    path = _plan(tmp_path / "plan.pdf")
    assert measure_page(path, 0, 20260925) == measure_page(path, 0, 20260925)
