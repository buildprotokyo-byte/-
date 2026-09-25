"""周21 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks.measure_dimension_chains import (
    GRID_MARK,
    chains,
    grid_marks,
    measure_page,
    scatter_ends,
)


def test_端点が繋がれば連なり() -> None:
    got = chains(
        [
            ("横", (0.0, 0.0), (10.0, 0.0)),
            ("横", (10.0, 0.0), (20.0, 0.0)),
        ]
    )
    assert got["横"] == 1


def test_1本では連なりではない() -> None:
    assert chains([("横", (0.0, 0.0), (10.0, 0.0))])["横"] == 0


def test_離れていれば繋がらない() -> None:
    got = chains(
        [
            ("横", (0.0, 0.0), (10.0, 0.0)),
            ("横", (100.0, 0.0), (110.0, 0.0)),
        ]
    )
    assert got["横"] == 0


def test_向きが違えば繋がらない() -> None:
    got = chains(
        [
            ("横", (0.0, 0.0), (10.0, 0.0)),
            ("縦", (10.0, 0.0), (10.0, 10.0)),
        ]
    )
    assert got["横"] == 0 and got["縦"] == 0


def test_囮は長さと向きを変えず位置だけ動かす() -> None:
    """**本数も長さも変えない。**変えるのは位置だけ。"""
    readings = [("横", (0.0, 0.0), (10.0, 0.0)), ("縦", (0.0, 0.0), (0.0, 7.0))]
    got = scatter_ends(readings, 200.0, 200.0, 20260925)
    assert len(got) == len(readings)
    for (_, start, end), (_, was_start, was_end) in zip(got, readings):
        assert end[0] - start[0] == pytest.approx(was_end[0] - was_start[0])
        assert end[1] - start[1] == pytest.approx(was_end[1] - was_start[1])


def test_通り芯の符号はXYの書き方しか拾わない() -> None:
    """**限界を試験で固定する。**A・1 だけの通り芯は拾えない。"""
    assert GRID_MARK.match("X1") and GRID_MARK.match("Y-2")
    assert not GRID_MARK.match("A")
    assert not GRID_MARK.match("1")
    assert not GRID_MARK.match("X123")


def test_紙から通り芯の符号を数える(tmp_path: Path) -> None:
    path = tmp_path / "grid.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "X1", fontsize=9)
    page.insert_text((20, 40), "Y2", fontsize=9)
    page.insert_text((20, 60), "3,640", fontsize=9)
    document.save(path)
    document.close()
    opened = pymupdf.open(path)
    try:
        assert grid_marks(opened[0]) == 2
    finally:
        opened.close()


def test_天井高の注記が無いページは平面図とみなさない(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "X1", fontsize=9)
    document.save(path)
    document.close()
    assert measure_page(path, 0, 20260925) == {"ページ": 1, "平面図とみなす": False}
