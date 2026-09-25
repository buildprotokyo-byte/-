"""周17 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from benchmarks.measure_room_face_link import (
    measure,
    measure_page,
    name_positions,
    reach,
    schedule_rooms,
)
from tests.test_pdf_tables import single_table_pdf

SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
FAR = ((100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0))

FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室", "床", "フローリング"),
    ("便所", "床", "長尺シート"),
)


def test_仕上表の室名は語の表を通さずに取る(tmp_path: Path) -> None:
    """**図面が室として並べたものを、こちらの語彙で落とさない。**"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    assert schedule_rooms(path) == ["洋室", "便所"]


def test_内側なら内側の面だけを返す() -> None:
    assert reach((5.0, 5.0), [SQUARE, FAR], 100.0) == [0]


def test_内側でなければ帯の中の面を返す() -> None:
    assert reach((5.0, -2.0), [SQUARE, FAR], 3.0) == [0]
    assert reach((5.0, -50.0), [SQUARE, FAR], 3.0) == []


def test_一致はそのまま含むこと(tmp_path: Path) -> None:
    path = tmp_path / "plan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "洋室(1)", fontsize=9, fontname="japan")
    page.insert_text((20, 60), "2,730", fontsize=9)
    document.save(path)
    document.close()
    opened = pymupdf.open(path)
    try:
        got = name_positions(opened[0], ["洋室", "便所"])
    finally:
        opened.close()
    assert [name for name, _ in got] == ["洋室"]


def _plan(path: Path) -> Path:
    """**合成の平面図**: 室 1 つ、その中に室名と天井高を刷る。"""
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 550, 350))
    shape.draw_line(pymupdf.Point(300, 50), pymupdf.Point(300, 350))
    for y in (120, 190, 270):
        shape.draw_line(pymupdf.Point(50, y), pymupdf.Point(300, y))
    shape.finish(width=1)
    shape.commit()
    page.insert_text((360, 130), "便所", fontsize=9, fontname="japan")
    page.insert_text((100, 160), "洋室", fontsize=9, fontname="japan")
    page.insert_text((150, 160), "CH=2400", fontsize=9)
    document.save(path)
    document.close()
    return path


def test_天井高の注記が無いページは平面図とみなさない(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "洋室", fontsize=9, fontname="japan")
    document.save(path)
    document.close()
    got = measure_page(path, 0, ["洋室"], 20260925)
    assert got == {"ページ": 1, "平面図とみなす": False}


def test_合成の平面図では室名が面に届く(tmp_path: Path) -> None:
    """**実図面に当てる前の確かめ**(周12 の教訓)。"""
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, ["洋室", "便所"], 20260925)
    assert got["室名が印字されていた数"] == 2
    assert got["線1_面に届いた"] == 2
    assert got["線3_面が1つに決まった室"] == 2


def test_囮は名前を持ったまま位置だけ動く(tmp_path: Path) -> None:
    """**線2 も線3 も、本物とまったく同じ数え方で比べられること。**"""
    got = measure_page(_plan(tmp_path / "plan.pdf"), 0, ["洋室", "便所"], 20260925)
    assert "線3_囮" in got and "線2_囮" in got
    assert got["線3_囮"] <= got["室名が印字されていた数"]


def test_室名は既定では返さない(tmp_path: Path) -> None:
    """**図面の中身はリポジトリにも記憶にも書かない**(取り決め)。"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    quiet = measure(path, 20260925, with_text=False)
    loud = measure(path, 20260925, with_text=True)
    assert "室名(共有フォルダにのみ置く)" not in quiet
    assert loud["室名(共有フォルダにのみ置く)"] == ["洋室", "便所"]


def test_同じ種なら同じ答えになる(tmp_path: Path) -> None:
    path = _plan(tmp_path / "plan.pdf")
    first = measure_page(path, 0, ["洋室", "便所"], 20260925)
    assert first == measure_page(path, 0, ["洋室", "便所"], 20260925)
