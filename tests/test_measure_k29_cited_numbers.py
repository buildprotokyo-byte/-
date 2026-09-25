"""引いた数字が印字されているかを数える道具の試験。**合成データだけ**を使う。"""

from __future__ import annotations

import json

import pymupdf
import pytest

from benchmarks.measure_k29_cited_numbers import (
    _formula_holds,
    _items,
    _printed,
    count,
    normalize,
    page_numbers,
)


@pytest.fixture()
def drawing(tmp_path):
    """合成の紙。**印字するのは 3 つの数字だけ。**"""
    path = tmp_path / "drawing.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(100, 100), "3,640", fontsize=8, fontname="helv")
    page.insert_text(pymupdf.Point(200, 100), "2730", fontsize=8, fontname="helv")
    page.insert_text(pymupdf.Point(300, 100), "1500", fontsize=8, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def _answer(page=1, texts=("3,640", "2730"), area=9.94, why="理由"):
    return {
        "kind": "面積を言えた",
        "area_sqm": area,
        "used_numbers": [{"page": page, "text": t, "where": "どこか"} for t in texts],
        "why": why,
        "formula": "式",
        "basis": "内法",
    }


class Test印字の実在:
    def test_印字されている数字は実在と数える(self, drawing):
        words = page_numbers(drawing, 0)
        assert _printed("3,640", words) is True
        assert _printed("2730", words) is True

    def test_桁区切りのゆれは同じものとして扱う(self, drawing):
        words = page_numbers(drawing, 0)
        assert _printed("3640", words) is True

    def test_記号が付いた書き方も数の部分で照らす(self, tmp_path):
        path = tmp_path / "approx.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=400)
        page.insert_text(pymupdf.Point(50, 50), "2430", fontsize=8, fontname="helv")
        doc.save(path)
        doc.close()
        assert _printed("≒2,430", page_numbers(path, 0)) is True

    def test_印字されていない数字は実在しないと数える(self, drawing):
        assert _printed("9999", page_numbers(drawing, 0)) is False

    def test_部分一致では実在と数えない(self, drawing):
        """`1` がどこかにあるだけで `1500` を実在にしない、の逆向き。"""
        assert _printed("15", page_numbers(drawing, 0)) is False

    def test_無いページを指したら実在しない(self, drawing):
        counts = count([_answer(page=99)], drawing)
        assert counts["T1 印字されていない"] == 2
        assert counts["T1 全部が印字だった答え"] == 0


class Test式の整合:
    def test_引いた2つの積で答えが作れる(self):
        assert _formula_holds(9.94, [3640.0, 2730.0]) is True

    def test_作れなければ通さない(self):
        assert _formula_holds(20.0, [3640.0, 2730.0]) is False

    def test_数字が1つでは作れない(self):
        assert _formula_holds(9.94, [3640.0]) is False

    def test_面積が無ければ作れない(self):
        assert _formula_holds(None, [3640.0, 2730.0]) is False

    def test_3つ以上からでも組を探す(self):
        assert _formula_holds(5.46, [1500.0, 3640.0, 2730.0]) is True


class Test数え方:
    def test_全部印字で式も作れる答え(self, drawing):
        counts = count([_answer()], drawing)
        assert counts["答えの数"] == 1
        assert counts["数字を引いた答え"] == 1
        assert counts["T1 印字されていた"] == 2
        assert counts["T1 全部が印字だった答え"] == 1
        assert counts["T2 式が作れた答え"] == 1

    def test_1つでも印字でなければ全部印字には数えない(self, drawing):
        counts = count([_answer(texts=("3,640", "9999"))], drawing)
        assert counts["T1 印字されていた"] == 1
        assert counts["T1 印字されていない"] == 1
        assert counts["T1 全部が印字だった答え"] == 0

    def test_数字も理由も無い答えは根拠が空に数える(self, drawing):
        item = {"kind": "面積を言えた", "area_sqm": 3.0, "used_numbers": [], "why": ""}
        counts = count([item], drawing)
        assert counts["根拠が空の答え"] == 1
        assert counts["数字を引いた答え"] == 0

    def test_理由があれば根拠が空には数えない(self, drawing):
        item = {"kind": "読めない", "area_sqm": None, "used_numbers": [], "why": "理由"}
        counts = count([item], drawing)
        assert counts["根拠が空の答え"] == 0


class Test答えのファイルの形:
    def test_室ごとに聞いた古い形を受ける(self):
        payload = {"R-01": _answer(), "R-02": _answer()}
        assert len(_items(payload)) == 2

    def test_場所を自分で選ばせた形も受ける(self):
        payload = {"items": [_answer(), _answer(), _answer()], "per_page": {}}
        assert len(_items(payload)) == 3

    def test_並びだけのファイルも受ける(self):
        assert len(_items([_answer()])) == 1

    def test_知らない形は空で返す(self):
        assert _items("文字列") == []


class Test正規化:
    def test_桁区切りと空白を落とす(self):
        assert normalize(" 3,640 ") == "3640"

    def test_全角を半角に揃える(self):
        assert normalize("１２３") == "123"

    def test_Noneは空文字になる(self):
        assert normalize(None) == ""


def test_実案件の数字を返り値に入れない(drawing):
    """**出すのは件数だけ。**引いた文字列そのものを返さない。"""
    counts = count([_answer()], drawing)
    assert all(isinstance(value, int) for value in counts.values())
    assert "3,640" not in json.dumps(counts, ensure_ascii=False)


class Test根拠が空の線:
    def test_面積を出していない答えは根拠が空に数えない(self, drawing):
        """**読めない・質疑は、根拠が空でも当たり前。**でたらめではない。"""
        item = {"kind": "読めない", "area_sqm": None, "used_numbers": [], "why": "",
                "reason": "図面のどこにも無い"}
        assert count([item], drawing)["根拠が空の答え"] == 0

    def test_数字を出したのに根拠が無ければ数える(self, drawing):
        item = {"kind": "面積を言えた", "area_sqm": 3.0, "used_numbers": [], "why": ""}
        assert count([item], drawing)["根拠が空の答え"] == 1
