"""紙の上の位置に関する共通の部品の試験。**合成の紙だけを使う。**実図面は読まない。

K-26 2 番。**表題欄の線引きが 8 つのファイルに写し取られていて、そのうち 5 つが
回転前の座標のままだった。**同じ誤りが 6 度目に起きないよう、規則を 1 か所に集める。
"""

from __future__ import annotations

import pymupdf
import pytest

from benchmarks.page_geometry import in_drawing, shown, title_block_top


def _put(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    """**表示の向き**で (x, y) に文字を置く。"""
    page.insert_text(
        pymupdf.Point(x, y) * ~page.rotation_matrix, text, fontsize=9, fontname="japan"
    )


def _sheet(rotation: int) -> pymupdf.Document:
    """A3 横の紙。表示の向きは回転の有無にかかわらず 1190.5 x 841.9 になる。"""
    doc = pymupdf.open()
    if rotation in (90, 270):
        page = doc.new_page(width=841.9, height=1190.5)
    else:
        page = doc.new_page(width=1190.5, height=841.9)
    page.set_rotation(rotation)
    _put(page, 40.0, 400.0, "ひだり")   # 図面の左端の帯。**表題欄ではない。**
    _put(page, 600.0, 400.0, "まんなか")
    _put(page, 600.0, 800.0, "したばた")  # 表示の向きの下端。**ここが表題欄。**
    return doc


@pytest.mark.parametrize("rotation", [0, 90, 270])
class Test表題欄の線引き:
    def _kept(self, rotation) -> list[str]:
        with _sheet(rotation) as doc:
            page = doc[0]
            return [w[4] for w in page.get_text("words") if in_drawing(page, w[:4])]

    def test_表題欄は外す(self, rotation):
        assert "したばた" not in self._kept(rotation)

    def test_図面の左端の帯は外さない(self, rotation):
        """**回転していないページでは、左端の帯は表題欄ではない。**"""
        assert "ひだり" in self._kept(rotation)

    def test_真ん中は残る(self, rotation):
        assert "まんなか" in self._kept(rotation)

    def test_表示の向きの高さで線を引く(self, rotation):
        """紙の向きが違っても、**表示の向きでの高さ**の同じ割合で線が引かれる。"""
        with _sheet(rotation) as doc:
            assert title_block_top(doc[0]) == pytest.approx(841.9 * 0.88, abs=0.1)

    def test_回転前の矩形を表示の向きに直す(self, rotation):
        """`shown` を通した矩形は、**どの回転でも同じ紙の中に収まる。**"""
        with _sheet(rotation) as doc:
            page = doc[0]
            for w in page.get_text("words"):
                box = shown(page, w[:4])
                assert 0 <= box.x0 <= 1191, f"回転 {rotation} で x がはみ出した"
                assert 0 <= box.y0 <= 842, f"回転 {rotation} で y がはみ出した"


def test_回転前の座標で線を引くと回転していないページで取り違える():
    """**直す前の規則を試験に残しておく。**これが元の誤りである。

    「回転前の x が 75pt より左を表題欄とする」は、回転していないページでは
    **図面の左端の帯**を捨て、**表題欄を残す。**
    """
    with _sheet(0) as doc:
        page = doc[0]
        words = {w[4]: w[:4] for w in page.get_text("words")}
        古い規則 = lambda bbox: bbox[0] >= 75.0  # noqa: E731
        assert 古い規則(words["したばた"]) is True, "古い規則では表題欄が残ってしまう"
        assert 古い規則(words["ひだり"]) is False, "古い規則では図面の左端を捨ててしまう"
        # **いまの規則はどちらも逆にする。**
        assert in_drawing(page, words["したばた"]) is False
        assert in_drawing(page, words["ひだり"]) is True
