"""周15 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks.measure_names_outside import (
    band_faces,
    build_check_sheet,
    check_definition,
    distance_to_boundary,
    measure_page,
    point_to_segment,
)

SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


def test_線分の端より外では端までの距離になる() -> None:
    """**線ではなく線分。**ここを間違えると帯が無限に伸びる。"""
    assert point_to_segment((20.0, 0.0), (0.0, 0.0), (10.0, 0.0)) == pytest.approx(10.0)
    assert point_to_segment((5.0, 3.0), (0.0, 0.0), (10.0, 0.0)) == pytest.approx(3.0)


def test_長さ0の線分でも落ちない() -> None:
    assert point_to_segment((3.0, 4.0), (0.0, 0.0), (0.0, 0.0)) == pytest.approx(5.0)


def test_輪郭までの距離は内側でも正の値() -> None:
    assert distance_to_boundary((5.0, 5.0), SQUARE) == pytest.approx(5.0)
    assert distance_to_boundary((5.0, -2.0), SQUARE) == pytest.approx(2.0)


def test_内側の点は帯に入らない() -> None:
    """**周14 で数えた分を二重に数えない。**"""
    assert band_faces([(5.0, 5.0)], [SQUARE], 100.0) == {}


def test_外の近くの点だけが帯に入る() -> None:
    got = band_faces([(5.0, -2.0), (5.0, -50.0)], [SQUARE], 3.0)
    assert got == {0: 1}


def test_隣の面の内側にある点は落とす() -> None:
    """**隣の室の名前を自分の名前として数えない。**"""
    neighbour = ((0.0, -10.0), (10.0, -10.0), (10.0, -1.0), (0.0, -1.0))
    got = band_faces([(5.0, -5.0)], [SQUARE, neighbour], 100.0)
    assert got == {}


def test_帯は外へ向いている(tmp_path: Path) -> None:
    """**実図面に当てる前の確かめ**(周12 の教訓)。"""
    got = check_definition(tmp_path)
    assert got["面"] == 1
    assert got["狭い帯_室名"] == 1
    assert got["広い帯_室名"] == 1
    assert got["狭い帯_天井高"] == 0


def test_天井高の注記が無いページは早く返す(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, 180, 180))
    shape.finish(width=1)
    shape.commit()
    document.save(path)
    document.close()
    assert measure_page(path, 0, 20260925) == {"ページ": 1, "注記": 0}


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    path = tmp_path / "check.pdf"
    build_check_sheet(path)
    assert measure_page(path, 0, 20260925) == measure_page(path, 0, 20260925)
