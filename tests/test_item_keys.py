"""突き合わせの鍵を揃える処理のテスト。**合成データだけを使う。**

33 周目(`docs/b_item_key_break_report.md`)で分かったことを、ここで押さえる。

**「壁軽量鉄骨天井下地撤去」と「天井軽量鉄骨壁下地撤去」は別の行である。**
壁に付いている天井下地と、天井に付いている壁下地は違う。文字は同じで並びだけが違うので、
**語順を無視して揃えると同じ鍵になってしまう。**
"""

from __future__ import annotations

import pytest

from arbitration.item_keys import (
    SCOPE_CONFIRM,
    SCOPE_TO_HUMAN,
    normalise_item_key,
    same_item_key,
)

#: 33 周目に見つかった、壊れる対。**この 2 つは別の行。**
WALL_CEILING = "壁軽量鉄骨天井下地撤去"
CEILING_WALL = "天井軽量鉄骨壁下地撤去"


def test_確定させる向きでは壁と天井が入れ替わった行を同じにしない() -> None:
    assert not same_item_key(WALL_CEILING, CEILING_WALL, scope=SCOPE_CONFIRM)


def test_人へ回す向きでは語順を無視するのでまとまる() -> None:
    # 広い側は「まとめすぎる」ことを承知で使う。人が見るので取り返せる。
    assert same_item_key(WALL_CEILING, CEILING_WALL, scope=SCOPE_TO_HUMAN)


@pytest.mark.parametrize(
    "変えたほう",
    [
        "ビニル・床タイル撤去",      # 中黒が入る
        "床タイル撤去(ビニル)",      # 先頭の語が括弧で末尾へ回る
        "ビニル床タイル撤去 ",        # 末尾に空白
    ],
)
def test_人へ回す向きでは表記のゆれが揃う(変えたほう: str) -> None:
    assert same_item_key("ビニル床タイル撤去", 変えたほう, scope=SCOPE_TO_HUMAN)


def test_人へ回す向きでは送り仮名のゆれも揃う() -> None:
    assert same_item_key("床タイル張り", "床タイル張", scope=SCOPE_TO_HUMAN)


def test_確定させる向きでも記号と長音のゆれは揃う() -> None:
    # 狭い側でも、記号と仮名の揺れだけは揃える(語順は動かさない)。
    assert same_item_key("カーペット撤去", "カ−ペット撤去", scope=SCOPE_CONFIRM)
    assert same_item_key("ビニル床タイル撤去", "ビニル・床タイル撤去", scope=SCOPE_CONFIRM)


def test_確定させる向きでは括弧を動かしても揃わない() -> None:
    # 括弧の中身を外に出すのは広い側だけの仕事。狭い側は動かさない。
    assert not same_item_key(
        "ビニル床タイル撤去", "床タイル撤去(ビニル)", scope=SCOPE_CONFIRM
    )


def test_向きを指定しなければ狭いほうになる() -> None:
    assert normalise_item_key(WALL_CEILING) == normalise_item_key(
        WALL_CEILING, scope=SCOPE_CONFIRM
    )
    assert not same_item_key(WALL_CEILING, CEILING_WALL)


def test_知らない向きはエラーになる() -> None:
    with pytest.raises(ValueError, match="向き"):
        normalise_item_key(WALL_CEILING, scope="なんとなく広め")


def test_空の鍵はエラーになる() -> None:
    with pytest.raises(ValueError, match="空"):
        normalise_item_key("   ")
