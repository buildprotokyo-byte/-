"""周29: **`app.py` が名前で引き当てている場所**の突き合わせを固定する。

**実案件の室名は書かない**(K-33)。合成の名前だけを使う。

周28 で「巾木の数量の名前が食い違う」を見つけ、**その種類の欠陥は
『作れなかった理由を残す』仕掛けでは原理的に拾えない**と分かった。
**拾えないなら数えるしかない。**ここはその数え方を試験に留めたものである。
"""

from __future__ import annotations

import app
from estimating.from_room_dimensions import (
    KIND_FLOOR_AREA,
    KIND_PERIMETER,
    KIND_WALL_AREA,
)


def test_引き当て側にあって作る側に無い名前は巾木の1件だけ() -> None:
    """**直したら落ちる試験。**落ちたら、この試験ごと消してよい。"""
    made = {KIND_FLOOR_AREA, KIND_PERIMETER, KIND_WALL_AREA}
    wanted = {spec[0] for spec in app.FINISH_PART_QUANTITY.values()}
    assert sorted(wanted - made) == ["周長"]
    assert sorted(made - wanted) == ["室の周長"]


def test_部位の名前は4つ() -> None:
    """**この 4 つに無い部位は「作り方をまだ持っていない」で行になる。**"""
    assert set(app.FINISH_PART_QUANTITY) == {"床", "天井", "壁", "巾木"}


def test_全角と半角は同じ名前として引き当たる() -> None:
    assert app._nfkc("洋室1") == app._nfkc("洋室１")


def test_前後の空白は落ちる() -> None:
    assert app._nfkc("洋室1 ") == app._nfkc("洋室1")


def test_中の空白も落ちる() -> None:
    """**周29 で予想が外れたところ。**中の空白まで落ちる。

    `_nfkc` は `"".join(NFKC(text).split())` なので、**空白を全部取り除く。**
    **だから空白だけが違う 2 つの室名は、同じ室として引き当たる。**
    """
    assert app._nfkc("洋 室1") == app._nfkc("洋室1")
    assert app._nfkc("洋　室1") == app._nfkc("洋室1")


def test_改行も落ちる() -> None:
    """**仕上表の室名は複数行のことがある。**改行は落ちて 1 つの名前になる。

    **人が打ち直すときに区切り文字(・ や /)を入れると引き当たらない。**
    """
    assert app._nfkc("キッチン\nダイニング") == app._nfkc("キッチンダイニング")
    assert app._nfkc("キッチン・ダイニング") != app._nfkc("キッチンダイニング")


def test_読みがなは別の名前のまま() -> None:
    """**取り違えを黙って吸収しない。**"""
    assert app._nfkc("ようしつ1") != app._nfkc("洋室1")
