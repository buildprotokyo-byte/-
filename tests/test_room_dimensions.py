"""9周目: 人が入れた室の寸法から数量を作る経路の試験。

**この試験が守りたいこと**(`docs/a2_human_room_dimensions_criteria.md`)

1. 縦・横・天井高を入れると、床面積・周長・内壁面積の 3 種類が出る。
2. **入っていない欄を既定値で埋めない。** 天井高が無ければ内壁面積は作らない。
3. **黙って確定しない。** 手法は未校正・上限 weak。
4. 作らなかったことには**理由が並ぶ**(「行が無い」で終わらせない)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arbitration.method_policies import (
    DEFAULT_METHOD_POLICIES,
    METHOD_HUMAN_ROOM_DIMENSIONS,
)
from estimating.from_room_dimensions import (
    KIND_FLOOR_AREA,
    KIND_PERIMETER,
    KIND_WALL_AREA,
    SOURCE_HUMAN_INPUT,
    WALL_OPENING_NOTE,
    quantities_from_room_dimensions,
)
from estimating.mapping import map_quantities
from estimating.rules import load_rules
from intake.room_dimensions import (
    METHOD_HUMAN_ROOM_DIMENSIONS as METHOD_IN_INTAKE,
    RoomDimension,
    RoomDimensionError,
)

ROOM_RULES = Path("estimating/examples/synthetic_room_rules.json")


def _room(name="居室", length=3640.0, width=2730.0, height=2400.0, basis="芯々"):
    return RoomDimension(
        room_name=name,
        length_mm=length,
        width_mm=width,
        ceiling_height_mm=height,
        area_basis=basis,
        entered_by="試験",
    )


def _kinds(result):
    return sorted(item.kind for item in result.quantities)


# --- 1. 3 種類の数量が出る ---------------------------------------------------


def test_縦横天井高を入れると3種類の数量が出る():
    result = quantities_from_room_dimensions([_room()])
    assert _kinds(result) == sorted([KIND_FLOOR_AREA, KIND_PERIMETER, KIND_WALL_AREA])


def test_床面積と周長と内壁面積の値が寸法どおりに出る():
    result = quantities_from_room_dimensions([_room()])
    by_kind = {item.kind: item for item in result.quantities}
    # 3640 x 2730 = 9.9372 ㎡、周長 12.74 m、壁 12.74 x 2.4 = 30.576 ㎡
    assert by_kind[KIND_FLOOR_AREA].value_range == (9.9372, 9.9372)
    assert by_kind[KIND_PERIMETER].value_range == (12.74, 12.74)
    assert by_kind[KIND_WALL_AREA].value_range == (30.576, 30.576)


def test_単位が正規形に直せる():
    result = quantities_from_room_dimensions([_room()])
    by_kind = {item.kind: item for item in result.quantities}
    assert by_kind[KIND_FLOOR_AREA].canonical_range == (99372, 99372)
    assert by_kind[KIND_PERIMETER].canonical_range == (12740, 12740)
    assert by_kind[KIND_WALL_AREA].canonical_range == (305760, 305760)


def test_刻みで表せない寸法は丸めずに範囲で出す():
    # 3641 x 2731 = 9943571 mm² = 99435.71 cm²。1cm² きざみでは表せない。
    result = quantities_from_room_dimensions([_room(length=3641.0, width=2731.0)])
    by_kind = {item.kind: item for item in result.quantities}
    low, high = by_kind[KIND_FLOOR_AREA].canonical_range
    assert (low, high) == (99435, 99436), "丸めずに下と上へ振ること"


# --- 2. 入っていない欄を埋めない ---------------------------------------------


def test_天井高が無いと内壁面積を出さない():
    result = quantities_from_room_dimensions([_room(height=None)])
    assert KIND_WALL_AREA not in _kinds(result)
    assert KIND_FLOOR_AREA in _kinds(result)


def test_天井高が無いことが理由として並ぶ():
    result = quantities_from_room_dimensions([_room(height=None)])
    assert any("天井高" in gap for gap in result.gaps)


def test_縦だけでは床面積も周長も出さない():
    result = quantities_from_room_dimensions([_room(width=None, height=None)])
    assert result.quantities == ()
    assert any("横" in gap for gap in result.gaps)


def test_天井高が無い室は壁面積の計算そのものがNoneを返す():
    """**入口の関門だけに頼らない。** 変換側が `has_walls` を見ているので、
    計算そのものを壊しても上の試験には映らない(ほかの関門に隠れる)。
    計算を直に呼んで、既定の天井高で埋めていないことを確かめる。
    """
    room = _room(height=None)
    assert room.wall_area_cm2_range() is None
    assert room.has_walls is False


def test_天井高だけ入れても何も出さない():
    """負の対照3。縦・横が無ければ床も壁も出ない。"""
    result = quantities_from_room_dimensions(
        [_room(length=None, width=None, height=2400.0)]
    )
    assert result.quantities == ()


def test_測り方が不明なら理由が並び芯々とは書かない():
    result = quantities_from_room_dimensions([_room(basis="不明")])
    assert any("測り方が不明" in gap for gap in result.gaps)
    floor = next(i for i in result.quantities if i.kind == KIND_FLOOR_AREA)
    assert any("芯々として扱っていない" in note for note in floor.notes)


# --- 3. 負の対照 -------------------------------------------------------------


def test_何も入れなければ数量は0件():
    """負の対照1。空の入力から数量が出たら、どこかで既定値を作っている。"""
    result = quantities_from_room_dimensions([])
    assert result.quantities == ()
    assert result.gaps == ()


@pytest.mark.parametrize("bad", [0.0, -3640.0])
def test_0や負の寸法は受け付けない(bad):
    """負の対照2。黙って直さない。

    **「小さすぎる」とは別の断り方をすること。** 0 や負は単位の取り違えでは
    ないので、「mm で入っているか確かめて」と言われても人は直しようがない。
    この区別が無いと、下の「小さすぎる」の関門だけで 0 も弾けてしまい、
    **0 を通す壊し方が試験に映らない。**
    """
    with pytest.raises(RoomDimensionError) as error:
        _room(length=bad)
    assert "正の数" in str(error.value)
    assert "小さすぎます" not in str(error.value)


def test_mで入れた寸法は小さすぎるとして止まる():
    # 3.64 と 3640 の取り違え。直した結果がもっともらしいので黙って直さない。
    with pytest.raises(RoomDimensionError) as error:
        _room(length=3.64)
    assert "mm" in str(error.value)


def test_室名が空なら受け付けない():
    with pytest.raises(RoomDimensionError):
        _room(name="   ")


def test_同じ入力を2回渡しても同じ結果になる():
    """負の対照4。"""
    first = quantities_from_room_dimensions([_room()])
    second = quantities_from_room_dimensions([_room()])
    assert [i.target for i in first.quantities] == [i.target for i in second.quantities]
    assert [i.value_range for i in first.quantities] == [
        i.value_range for i in second.quantities
    ]


def test_室名を入れ替えても数量の値は変わらない():
    """負の対照5。室名が寸法の解釈に効いてはいけない。"""
    original = quantities_from_room_dimensions(
        [_room(name="居室"), _room(name="納戸", length=3000.0, width=2000.0)]
    )
    swapped = quantities_from_room_dimensions(
        [_room(name="納戸"), _room(name="居室", length=3000.0, width=2000.0)]
    )
    assert sorted(i.value_range for i in original.quantities) == sorted(
        i.value_range for i in swapped.quantities
    )


def test_同じ室名が2回あるとどちらも数量にしない():
    result = quantities_from_room_dimensions([_room(name="居室"), _room(name="居室")])
    assert len([i for i in result.quantities if i.kind == KIND_FLOOR_AREA]) == 1
    assert any("同じ室名が2回" in gap for gap in result.gaps)


# --- 4. 黙って確定しない -----------------------------------------------------


def test_手法は未校正で上限はweak():
    policy = DEFAULT_METHOD_POLICIES[METHOD_HUMAN_ROOM_DIMENSIONS]
    assert policy.calibrated is False
    assert policy.max_strength == "weak"


def test_手法IDが入口と登録簿で同じ():
    assert METHOD_IN_INTAKE == METHOD_HUMAN_ROOM_DIMENSIONS


def test_数量に確定の印が付いていない():
    result = quantities_from_room_dimensions([_room()])
    for item in result.quantities:
        assert item.action is None
        assert item.confirmed_range is None
        assert item.is_confirmed is False


def test_見積の行に確定したものが1件も出ない():
    result = quantities_from_room_dimensions([_room()])
    mapped = map_quantities(result.quantities, load_rules(ROOM_RULES))
    assert mapped.settled_lines() == ()


def test_見積の行まで届く():
    result = quantities_from_room_dimensions([_room()])
    mapped = map_quantities(result.quantities, load_rules(ROOM_RULES))
    kinds = {m.quantity.kind for m in mapped.mappings if m.lines}
    assert {KIND_FLOOR_AREA, KIND_PERIMETER, KIND_WALL_AREA} <= kinds


def test_規則が当たらなかった数量は捨てられない():
    result = quantities_from_room_dimensions([_room()])
    mapped = map_quantities(result.quantities, load_rules(ROOM_RULES))
    assert len(mapped.mappings) == len(result.quantities)


# --- 5. 根拠に残すこと -------------------------------------------------------


def test_独立でないことが根拠に残る():
    result = quantities_from_room_dimensions([_room()])
    for item in result.quantities:
        assert "独立" in item.provenance["independence_note"]


def test_出どころが図面ではなく人になっている():
    """**定数どうしを比べない。** 定数を書き換える壊し方が映らなくなる。"""
    assert SOURCE_HUMAN_INPUT == "human"
    result = quantities_from_room_dimensions([_room()])
    for item in result.quantities:
        assert item.source_kind == "human"
        assert item.source_kind != "drawing"
        assert item.method_id == "human_room_dimensions"


def test_内壁面積に開口を引いていない注記が付く():
    result = quantities_from_room_dimensions([_room()])
    wall = next(i for i in result.quantities if i.kind == KIND_WALL_AREA)
    assert WALL_OPENING_NOTE in wall.notes


def test_長方形とみなしていることが注記に残る():
    result = quantities_from_room_dimensions([_room()])
    for item in result.quantities:
        assert any("長方形" in note for note in item.notes)
