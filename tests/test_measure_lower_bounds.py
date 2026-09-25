"""周13 の測定の道具のテスト。**合成のページだけを使う。実図面は使わない。**"""

from __future__ import annotations

from axes.image_axis.pdf_room_outlines import (
    ROOM_MIN_SQM,
    ROOM_MIN_WIDTH_MM,
    find_room_outlines,
)
from axes.image_axis.pdf_vector_symbols import DrawingScale
from benchmarks.measure_lower_bounds import (
    STEPS,
    build_plain_room,
    build_room_with_fixture,
    cavities,
    check_definition,
)

SCALE = DrawingScale(denominator=50.0, source_text="1/50")


def test_今の既定値が段の先頭にある() -> None:
    assert STEPS[0] == (ROOM_MIN_WIDTH_MM, ROOM_MIN_SQM)


def test_段は下がっていく() -> None:
    widths = [w for w, _ in STEPS]
    assert widths == sorted(widths, reverse=True)


def test_家具の線1本で輪が割れる(tmp_path) -> None:
    """**この周の疑いの、仕組みの側。**線が一周を割ることを固定する。"""
    plain = tmp_path / "plain.pdf"
    fixture = tmp_path / "fixture.pdf"
    build_plain_room(plain)
    build_room_with_fixture(fixture)
    assert len(find_room_outlines(plain, 0, SCALE)) == 1
    assert len(find_room_outlines(fixture, 0, SCALE)) == 2


def test_合成で確かめるのは輪が割れることまで(tmp_path) -> None:
    """**破片が落ちるかは合成では確かめない**(条件の言い換えになるため)。"""
    result = check_definition(tmp_path)
    assert result["通過"] is True
    assert "破片が落ちるかは実図面で見る" in result["確かめたのは"]


def test_ふつうの室は壁の中身と判定されない(tmp_path) -> None:
    path = tmp_path / "plain.pdf"
    build_plain_room(path)
    assert cavities(find_room_outlines(path, 0, SCALE)) == 0


def test_壁の中身の判定は実装のものをそのまま使う(tmp_path) -> None:
    """**狭いだけでは壁ではない。狭くて長いのが壁である。**"""
    from axes.image_axis.pdf_room_outlines import is_wall_cavity

    # 細長い: 幅 150mm、長さ 6,000mm 相当
    assert is_wall_cavity((0.9, 12_300.0, 150.0), ROOM_MIN_WIDTH_MM) is True
    # 狭いが正方形に近い
    assert is_wall_cavity((0.09, 1_200.0, 300.0), ROOM_MIN_WIDTH_MM) is False
