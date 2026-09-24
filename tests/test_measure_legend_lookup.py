"""照合を数える道具の試験。**合成の紙だけを使う。**実図面は読まない。

K-24 2 番。**表題欄を外す線引きが、回転していないページでは効いていなかった。**
"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.measure_legend_lookup import _coloured_words, _words


def _put(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    """**表示の向き**で (x, y) に文字を置く。"""
    point = pymupdf.Point(x, y) * ~page.rotation_matrix
    page.insert_text(point, text, fontsize=9, fontname="japan")


def _sheet(rotation: int) -> pymupdf.Document:
    """A3 横の紙。表示の向きは回転の有無にかかわらず 1190.5 x 841.9 になる。"""
    doc = pymupdf.open()
    if rotation in (90, 270):
        page = doc.new_page(width=841.9, height=1190.5)
    else:
        page = doc.new_page(width=1190.5, height=841.9)
    page.set_rotation(rotation)
    # 図面の左端の帯(表示の向き)。**ここは表題欄ではない。**
    _put(page, 40.0, 400.0, "ひだり")
    # 図面の真ん中。
    _put(page, 600.0, 400.0, "まんなか")
    # 表示の向きで下端の帯。**ここが表題欄。**
    _put(page, 600.0, 800.0, "したばた")
    return doc


@pytest.mark.parametrize("rotation", [0, 90, 270])
class TestTitleBlock:
    def test_表題欄は表示の向きの下端で外す(self, rotation):
        with _sheet(rotation) as doc:
            got = _words(doc[0])
        assert "したばた" not in got, f"回転 {rotation} で表題欄が残っている"

    def test_図面の左端の帯は外さない(self, rotation):
        """**回転していないページでは、左端の帯は表題欄ではない。**"""
        with _sheet(rotation) as doc:
            got = _words(doc[0])
        assert "ひだり" in got, f"回転 {rotation} で図面の左端が捨てられている"

    def test_真ん中は残る(self, rotation):
        with _sheet(rotation) as doc:
            got = _words(doc[0])
        assert "まんなか" in got

    def test_色つきの語も同じ線引きで外す(self, rotation):
        with _sheet(rotation) as doc:
            got = [text for text, _ in _coloured_words(doc[0])]
        joined = "".join(got)
        assert "したばた" not in joined, f"回転 {rotation} で表題欄が色つきの側に残っている"
        assert "ひだり" in joined, f"回転 {rotation} で左端が色つきの側から消えている"


@pytest.mark.parametrize("rotation", [0, 90, 270])
class TestBuilderTitleBlock:
    """対照表を作る側も同じ線引きにする(K-24 2 番)。

    この道具は 270 度回転した凡例のページしか読まないので影響は出ていなかったが、
    **同じ誤りを残さない。**
    """

    def test_表題欄は外し図面の左端は残す(self, rotation):
        from benchmarks.build_legend_lookup import _words as builder_words

        with _sheet(rotation) as doc:
            got = [w[4] for w in builder_words(doc[0])]
        assert "したばた" not in got, f"回転 {rotation} で表題欄が残っている"
        assert "ひだり" in got, f"回転 {rotation} で図面の左端が捨てられている"
