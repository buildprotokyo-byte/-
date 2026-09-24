"""紙の上の位置についての、**測定の道具どうしで共有する規則。**

なぜこのファイルがあるか(K-26 2 番)
------------------------------------
表題欄を外す線引きは、**8 つのファイルに写し取られていた。**そのうち 5 つは
``TITLE_BLOCK_X = 75.0``(**回転前の x**)のままで、K-24 で 3 つだけを直したあとも
誤ったままだった。

**写した先を数え落とすと、同じ誤りが残る。**だから規則をここ 1 か所に置き、
測定の道具はここから読む。

元の誤り
--------
「**回転前の x が 75pt より左**を表題欄とする」。

P011 匿名化v2 では、**凡例のページ(270 度回転)ではこれが表示の向きの下端に当たる**
ので合っていた。ところが**回転していない 31 ページでは、表題欄ではなく図面の
左端の帯を捨て、表題欄の文字は残していた。****回転を前提にした値が、回転していない
ページで逆に働いた。**

**ページの座標に関する値は、回転の有無で意味が変わる。**
だからここでは**表示の向きに直してから**線を引く。
"""

from __future__ import annotations

import pymupdf

#: 表題欄は**表示の向きで**紙の下端にある。ページの高さのこの割合より下が表題欄。
#: 事務所名・個人名・登録番号が入るので**数にも入れない。**
#:
#: **1 案件でしか確かめていない**(`docs/provisional_decisions.md`)。
TITLE_BLOCK_BOTTOM = 0.88


def shown(page: pymupdf.Page, bbox) -> pymupdf.Rect:
    """回転前の矩形を**表示の向き**に直す。

    ``page.get_text`` と ``page.get_drawings`` が返すのは**回転前**の座標である。
    ``page.rect`` も回転前なので、**紙の向きを前提にした線引きは、必ずここを通す。**
    """
    return pymupdf.Rect(bbox) * page.rotation_matrix


def title_block_top(page: pymupdf.Page) -> float:
    """表題欄の上端(**表示の向きの y**)。"""
    return page.rect.height * TITLE_BLOCK_BOTTOM


def in_drawing(page: pymupdf.Page, bbox) -> bool:
    """その矩形が**表題欄の外**(= 図面の側)か。

    回転前の矩形を渡してよい。中で表示の向きに直す。
    """
    return shown(page, bbox).y0 < title_block_top(page)
