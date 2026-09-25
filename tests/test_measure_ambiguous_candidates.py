"""周25 の道具(`benchmarks/measure_ambiguous_candidates.py`)の試験。

**実図面は使わない。**手で置いた値で、数え方だけを固定する。
"""

from __future__ import annotations

from benchmarks.measure_ambiguous_candidates import (
    CONTROL_SHARE,
    MATCH_TOLERANCE,
    SCALE_TOLERANCE,
    _value_mm,
    check_definition,
    matching_candidates,
)

#: 1/50 のとき 1pt = 17.64mm。
MM_PER_POINT = 17.64


def test_ちょうど合う候補を1つ数える() -> None:
    assert matching_candidates(3640.0, [206.35, 120.0], MM_PER_POINT) == 1


def test_1パーセントの外は数えない() -> None:
    assert matching_candidates(3640.0, [210.0, 120.0], MM_PER_POINT) == 0


def test_2つとも合えば2と数える() -> None:
    assert matching_candidates(3640.0, [206.35, 206.4], MM_PER_POINT) == 2


def test_長さ0の候補は数えない() -> None:
    assert matching_candidates(3640.0, [0.0], MM_PER_POINT) == 0


def test_候補が無ければ0() -> None:
    assert matching_candidates(3640.0, [], MM_PER_POINT) == 0


def test_寸法にならない文字は値を返さない() -> None:
    assert _value_mm("AW-1") is None
    assert _value_mm("1/50") is None


def test_2桁以下の裸の数字は落とす() -> None:
    assert _value_mm("90") is None
    assert _value_mm("910") == 910.0


def test_単位つきはミリに直す() -> None:
    assert _value_mm("3.64m") == 3640.0
    assert _value_mm("364cm") == 3640.0


def test_桁区切りのカンマを読む() -> None:
    assert _value_mm("3,640") == 3640.0


def test_合成の確かめ() -> None:
    got = check_definition()
    assert got["ちょうど合う候補を拾う"] == 1
    assert got["1%_の外は拾わない"] == 0
    assert got["2つとも合えば2と数える"] == 2
    assert got["値が読めない文字は None"] is None
    assert got["2桁は落とす"] is None
    assert got["単位つきは直す"] == 3640.0


def test_しきいは基準のまま() -> None:
    assert (SCALE_TOLERANCE, MATCH_TOLERANCE, CONTROL_SHARE) == (0.01, 0.01, 0.9)
