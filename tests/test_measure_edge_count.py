"""周19 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from benchmarks.measure_edge_count import ROOM_MAX_EDGES, band_of, measure_page


def test_帯は辺の数で分かれる() -> None:
    assert band_of(4) == "4〜12本"
    assert band_of(12) == "4〜12本"
    assert band_of(13) == "13〜20本"
    assert band_of(50) == "21〜50本"
    assert band_of(2093) == "201本以上"


def test_辺が3本でも落ちない() -> None:
    """**帯の外の値で例外にしない。**"""
    assert band_of(3) == "4本未満"


def _plan(path: Path) -> Path:
    """**合成の紙**: 四角い室(辺は少ない)に室名と天井高を刷る。"""
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
    page.insert_text((150, 160), "CH=2400", fontsize=9)
    page.insert_text((360, 130), "便所", fontsize=9, fontname="japan")
    document.save(path)
    document.close()
    return path


def test_合成の四角い室は辺の数で通る(tmp_path: Path) -> None:
    """**実図面に当てる前の確かめ。**四角い室は 4 本なので必ず残る。"""
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, ["洋室", "便所"], 20260925)
    assert got[f"線1_辺が{ROOM_MAX_EDGES}本以下の面"] == got["幅600mm以上の面"]
    assert got["辺の数の帯"].get("4〜12本") == got["幅600mm以上の面"]


def test_合成の紙では天井高も室名も入る(tmp_path: Path) -> None:
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, ["洋室", "便所"], 20260925)
    assert got["線2_天井高が入った面"] >= 1
    assert got["線3_室名が1つに決まった面"] >= 1


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


def test_囮の欄が本物と同じ数え方で出る(tmp_path: Path) -> None:
    """**囮が無いまま通過と書けないこと。**"""
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, ["洋室", "便所"], 20260925)
    assert "線2_囮" in got and "線3_囮" in got


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    path = _plan(tmp_path / "plan.pdf")
    first = measure_page(path, 0, ["洋室", "便所"], 20260925)
    assert first == measure_page(path, 0, ["洋室", "便所"], 20260925)
