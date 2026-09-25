"""周16 の数え方の試験。**合成の紙だけで書く。実図面は使わない。**"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from benchmarks.measure_schedule_source import (
    appears_in,
    collect,
    distinct_room_words,
    measure,
    page_texts,
    plan_pages,
)
from tests.test_pdf_tables import single_table_pdf

FINISH_ROWS: tuple[tuple[str | None, ...], ...] = (
    ("室名", "部位", "仕上"),
    ("洋室", "床", "フローリング"),
    ("洋室", "壁", "クロス"),
    ("便所", "床", "長尺シート"),
    ("収納", "壁", "クロス"),
)


def test_仕上表から室名が取れる(tmp_path: Path) -> None:
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    got = collect(path)
    assert distinct_room_words(got["室名"]) == ["洋室", "便所", "収納"]


def test_囮Aは室名ではない列から同じ数だけ取る(tmp_path: Path) -> None:
    """**行ごとに 1 つなので、本物と同じ数の標本になる。**"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    got = collect(path)
    assert len(got["囮A"]) == len(got["室名"])
    assert "洋室" not in got["囮A"]


def test_囮Aは区分の語にほとんど当たらない(tmp_path: Path) -> None:
    """部位と仕上の文字は室名ではないので、区分の語に当たらないはず。"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    got = collect(path)
    assert distinct_room_words(got["囮A"]) == []


def test_仕上表が無ければ何も取れない(tmp_path: Path) -> None:
    """**0 件は「仕上表が無い」ではなく「この見出しでは当たらなかった」。**"""
    other = (("記号", "備考"), ("A", "あ"), ("B", "い"))
    path = single_table_pdf(tmp_path / "other.pdf", other)
    got = collect(path)
    assert got["室名"] == []
    assert got["ページごと"] == []


def test_重複は除く(tmp_path: Path) -> None:
    assert distinct_room_words(["洋室", "洋室", "便所"]) == ["洋室", "便所"]


def test_区分の語に当たらない文字は落とす(tmp_path: Path) -> None:
    assert distinct_room_words(["2,730", "X-1"]) == []


def test_一致はそのまま含むこと() -> None:
    assert appears_in("洋室", ["洋室(1)", "2,730"])
    assert not appears_in("便所", ["洋室(1)", "2,730"])


def test_平面図のページは天井高の注記で決める(tmp_path: Path) -> None:
    """**周5・周14・周15 と同じ決め方。ここを変えると周をまたいで比べられない。**"""
    path = tmp_path / "plan.pdf"
    document = pymupdf.open()
    document.new_page(width=200, height=200)
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "CH=2400", fontsize=9)
    document.save(path)
    document.close()
    assert plan_pages(path) == [1]
    assert "CH=2400" in page_texts(path, [1])


def test_室名も囮も既定では返さない(tmp_path: Path) -> None:
    """**図面の中身はリポジトリにも記憶にも書かない**(取り決め)。"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    quiet = measure(path, with_text=False)
    loud = measure(path, with_text=True)
    assert "文字(共有フォルダにのみ置く)" not in quiet
    assert loud["文字(共有フォルダにのみ置く)"]["室名"] == ["洋室", "便所", "収納"]


def test_線の判定は先に決めた幅で出る(tmp_path: Path) -> None:
    """室名 3 個・囮 0 個は**差 3 個で、合格の幅 5 個に届かない。**"""
    path = single_table_pdf(tmp_path / "finish.pdf", FINISH_ROWS)
    got = measure(path, with_text=False)
    assert got["線1_仕上表から室名が取れるか"]["本物"] == 3
    assert got["線1_仕上表から室名が取れるか"]["囮A"] == 0
    assert got["線1_仕上表から室名が取れるか"]["通過"] is False
