"""周14 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from benchmarks.measure_where_faces_are import (
    LIVING,
    OTHER,
    SERVICE,
    build_check_sheet,
    check_definition,
    classify,
    faces_with_any,
    measure_page,
    ratio,
    room_name_spans,
)


def test_居室の語は居室に分かれる() -> None:
    assert classify("洋室1") == LIVING
    assert classify("LDK") == LIVING


def test_非居室の語は非居室に分かれる() -> None:
    assert classify("便所") == SERVICE
    assert classify("押入") == SERVICE


def test_表に無い語はその他に落ちる() -> None:
    """**その他の件数を報告に出すのは、この落ち方があるから。**"""
    assert classify("3,640") == OTHER
    assert classify("X-1") == OTHER


def test_ホール階段は非居室に寄せる() -> None:
    """「ホール」は居室側にもあるが、**狭い意味の語を先に見る。**"""
    assert classify("ホール階段") == SERVICE
    assert classify("ホール") == LIVING


def test_室名らしくない文字は落とす() -> None:
    spans = [("洋室", (1.0, 1.0)), ("2,730", (2.0, 2.0)), ("便所", (3.0, 3.0))]
    got = room_name_spans(spans)
    assert [kind for _, _, kind in got] == [LIVING, SERVICE]


def test_数えるのは面の数で点の数ではない() -> None:
    square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    far = ((100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0))
    points = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    assert faces_with_any(points, [square, far]) == 1


def test_どちらも0なら割合は無い() -> None:
    assert ratio(0, 0) is None
    assert ratio(1, 1) == pytest.approx(0.5)


def test_合成の紙では区分がそのとおりに分かれる(tmp_path: Path) -> None:
    """**この確かめが通らないうちは実図面に当てない**(周11 の教訓)。"""
    got = check_definition(tmp_path)
    assert got["面の側_居室"] == 1
    assert got["面の側_非居室"] == 2
    assert got["天井高が入った面"] == 1
    assert got["室名が1つ入った面"] == 3


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
    got = measure_page(path, 0, 20260925, with_text=False)
    assert got == {"ページ": 1, "注記": 0}


def test_室名の文字は既定では返さない(tmp_path: Path) -> None:
    """**図面の中身はリポジトリにも記憶にも書かない**(取り決め)。"""
    path = tmp_path / "check.pdf"
    build_check_sheet(path)
    quiet = measure_page(path, 0, 20260925, with_text=False)
    loud = measure_page(path, 0, 20260925, with_text=True)
    assert "室名の文字(共有フォルダにのみ置く)" not in quiet
    assert loud["室名の文字(共有フォルダにのみ置く)"]


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    """囮は種で決まる。**測り直すたびに答えが変わらないこと。**"""
    path = tmp_path / "check.pdf"
    build_check_sheet(path)
    first = measure_page(path, 0, 20260925, with_text=False)
    second = measure_page(path, 0, 20260925, with_text=False)
    assert first == second
