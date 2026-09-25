"""**`app.py` が探す数量の名前と、実際に作られる名前の食い違いを固定する。**

**この試験は、直っていない欠陥をその場に留めておくためのものである。**
2026-09-25 の周28 で見つけた。

見つけ方
--------
合成の室の寸法を 10 室ぶん渡して `app.py` を一本通したところ、
**数量が入ったのは 51 行で、単位 `m` の 16 行(巾木)は 1 件も入らなかった。**
その 16 行には「数量は人の入力(室の寸法)を待っている」と書かれていたが、
**人の入力は渡してある。**

何が起きているか
----------------
- `app.py` の `FINISH_PART_QUANTITY` は、巾木の数量を **``"周長"``** で探す
- `estimating/from_room_dimensions.py` が作るのは **``"室の周長"``**(`KIND_PERIMETER`)

**名前が違うので、引き当ては永久に外れる。**
**人が何を入れても、巾木の 16 行に数量は入らない。**

なぜ直さずに固定するのか
------------------------
**K-29 で「`app.py` と `intake/` には触らない」と決まっている。**
直しは判断待ち(周28 の報告と PR)に回す。
**ただし黙って持ち越すと、この食い違いは次のスレッドから見えなくなる**
(周45 の教訓: **コメントが正しいことと、守られていることは別**)。
**だからここに留める。**

**直したときは、この試験は落ちる。落ちたら、この試験ごと消してよい。**
"""

from __future__ import annotations

import app
from estimating.from_room_dimensions import (
    KIND_FLOOR_AREA,
    KIND_PERIMETER,
    KIND_WALL_AREA,
    quantities_from_room_dimensions,
)
from intake.room_dimensions import RoomDimension


def _one_room() -> RoomDimension:
    return RoomDimension(
        room_name="合成の室",
        length_mm=4000.0,
        width_mm=3000.0,
        ceiling_height_mm=2400.0,
        area_basis="芯々",
        entered_by="合成(周28 の試験)",
    )


def test_床と壁と天井の名前は合っている() -> None:
    """**食い違っているのは巾木だけである**ことを先に固定する。"""
    wanted = {spec[0] for part, spec in app.FINISH_PART_QUANTITY.items() if part != "巾木"}
    assert wanted == {KIND_FLOOR_AREA, KIND_WALL_AREA}


def test_巾木だけ名前が食い違っている() -> None:
    """**直したら落ちる試験。**落ちたら、この試験ごと消してよい。"""
    assert app.FINISH_PART_QUANTITY["巾木"][0] == "周長"
    assert KIND_PERIMETER == "室の周長"
    assert app.FINISH_PART_QUANTITY["巾木"][0] != KIND_PERIMETER


def test_人が寸法を入れても巾木の引き当ては外れる() -> None:
    """**人の入力は届いている。届いていないのは名前のほうである。**"""
    result = quantities_from_room_dimensions([_one_room()])
    made = {item.target.partition("::")[0] for item in result.quantities}
    assert KIND_PERIMETER in made
    assert app.FINISH_PART_QUANTITY["巾木"][0] not in made


def test_作れなかった理由は残っていない() -> None:
    """**`from_room_dimensions` は 3 種類とも作れているので、`gaps` は空。**

    つまり**「なぜ作らなかったか」を残す仕掛けでは、この食い違いは拾えない。**
    作る側は作れているからである。
    """
    result = quantities_from_room_dimensions([_one_room()])
    assert result.gaps == ()
    assert len(result.quantities) == 3
