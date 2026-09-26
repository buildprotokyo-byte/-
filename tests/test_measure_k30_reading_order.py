"""読み手の答えを図面と突き合わせる道具の試験。**合成データだけ**を使う。

基準は `docs/k30_reading_order_criteria.md`(測る前にコミット済み)。
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from benchmarks.measure_k30_reading_order import (
    _evidence,
    measure,
    normalize,
    page_words,
    printed_exact,
    printed_run,
)


@pytest.fixture()
def drawing(tmp_path):
    """合成の紙。**表題部に縮尺と図面名を印字する。**"""
    path = tmp_path / "d.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text(pymupdf.Point(900, 800), "縮尺", fontsize=8, fontname="japan")
    page.insert_text(pymupdf.Point(950, 800), "1/50", fontsize=8, fontname="japan")
    page.insert_text(pymupdf.Point(900, 780), "図面名称", fontsize=8, fontname="japan")
    page.insert_text(pymupdf.Point(960, 780), "平面図", fontsize=8, fontname="japan")
    page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(100, 10))
    doc.new_page(width=1190, height=842)  # 2 ページ目: 図形も文字も無い
    doc.save(path)
    doc.close()
    return path


def _item(page=1, **kwargs):
    base = {
        "page": page,
        "drawing_kind": "平面",
        "kind_evidence": ["平面図"],
        "scale_denominator": 50,
        "scale_evidence": ["1/50"],
        "expression_evidence": [],
        "unreadable_reason": None,
    }
    base.update(kwargs)
    return base


class Test印字の実在:
    def test_印字されている語は完全一致で当たる(self, drawing):
        with pymupdf.open(drawing) as doc:
            words, _ = page_words(doc.load_page(0))
        assert printed_exact("平面図", words) is True

    def test_印字されていない語は当たらない(self, drawing):
        with pymupdf.open(drawing) as doc:
            words, _ = page_words(doc.load_page(0))
        assert printed_exact("断面図", words) is False

    def test_部分一致では当てない(self, drawing):
        """**K-29 の T1 と同じ。**「平面」が「平面図」に当たってはいけない。"""
        with pymupdf.open(drawing) as doc:
            words, _ = page_words(doc.load_page(0))
        assert printed_exact("平面", words) is False

    def test_語を繋いだ形は別に数える(self, drawing):
        """**基準に無い緩めた数え方。**採否の線には使わない。"""
        with pymupdf.open(drawing) as doc:
            _, ordered = page_words(doc.load_page(0))
        assert printed_run("縮尺1/50", ordered) is True

    def test_繋いでも無い語は当たらない(self, drawing):
        with pymupdf.open(drawing) as doc:
            _, ordered = page_words(doc.load_page(0))
        assert printed_run("天井伏図", ordered) is False


class Test縮尺:
    def test_印字と合えば合ったと数える(self, drawing):
        result = measure(drawing, {"items": [_item()]})
        assert result["pages"][0]["縮尺が合った"] is True

    def test_外れれば合わなかったと数える(self, drawing):
        result = measure(drawing, {"items": [_item(scale_denominator=100)]})
        assert result["pages"][0]["縮尺が合った"] is False

    def test_許容差の中なら合ったと数える(self, drawing):
        """**±5%。緩めない。**"""
        assert measure(drawing, {"items": [_item(scale_denominator=52)]})["pages"][0][
            "縮尺が合った"
        ] is True

    def test_許容差を超えれば外れ(self, drawing):
        assert measure(drawing, {"items": [_item(scale_denominator=53)]})["pages"][0][
            "縮尺が合った"
        ] is False

    def test_答えていなければ突き合わせない(self, drawing):
        result = measure(drawing, {"items": [_item(scale_denominator=None)]})
        assert result["pages"][0]["縮尺が合った"] is None
        assert result["summary"]["縮尺を突き合わせられたページ"] == 0


class Test読めないページ:
    def test_図形が無いページで読めないと答えれば通る(self, drawing):
        item = _item(page=2, scale_denominator=None, kind_evidence=[], scale_evidence=[],
                     unreadable_reason="図形も文字も無い")
        assert measure(drawing, {"items": [item]})["summary"]["線丙(図形が無いページで読めないと答え、縮尺を埋めなかった)"] is True

    def test_図形が無いのに縮尺を埋めたら落ちる(self, drawing):
        item = _item(page=2, unreadable_reason=None)
        assert measure(drawing, {"items": [item]})["summary"]["線丙(図形が無いページで読めないと答え、縮尺を埋めなかった)"] is False


class Test根拠を集める:
    def test_3つの欄から集める(self):
        item = _item(expression_evidence=[{"what": "太い線", "printed": ["W1"]}])
        assert sorted(_evidence(item)) == ["1/50", "W1", "平面図"]

    def test_空の欄は数えない(self):
        assert _evidence(_item(kind_evidence=[], scale_evidence=[])) == []

    def test_表現の欄が辞書でなければ落とす(self):
        assert _evidence(_item(kind_evidence=[], scale_evidence=[],
                               expression_evidence=["文字列"])) == []


class Test正規化:
    def test_全角と空白を均す(self):
        assert normalize(" １／５０ ") == "1/50"

    def test_Noneは空文字(self):
        assert normalize(None) == ""


def test_実案件の文字を返り値に入れない(drawing):
    """**出すのは件数だけ。**根拠の文字列そのものを返さない。"""
    result = measure(drawing, {"items": [_item()]})
    assert "平面図" not in json.dumps(result, ensure_ascii=False)
