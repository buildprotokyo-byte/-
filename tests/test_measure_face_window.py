"""周12 の測定の道具のテスト。**合成のページだけを使う。実図面は使わない。**"""

from __future__ import annotations

from axes.image_axis.pdf_room_outlines import find_room_outlines
from axes.image_axis.pdf_vector_symbols import DrawingScale
from benchmarks.measure_face_window import (
    CAPS_SQM,
    build_split_sheet,
    build_whole_floor_sheet,
    check_definition,
)

SCALE = DrawingScale(denominator=50.0, source_text="1/50")


def test_今の上限が測る段の先頭にある() -> None:
    """**200㎡ が今の既定値**で、そこから下げていく。"""
    assert CAPS_SQM[0] == 200.0
    assert list(CAPS_SQM) == sorted(CAPS_SQM, reverse=True)


def test_仕切りを描かない紙は面が1つになる(tmp_path) -> None:
    """**これがこの周の疑いそのもの。**仕切りが閉じていなければ割れない。"""
    path = tmp_path / "whole.pdf"
    build_whole_floor_sheet(path)
    faces = find_room_outlines(path, 0, SCALE)
    assert len(faces) == 1


def test_仕切りを描いた紙は面が割れる(tmp_path) -> None:
    path = tmp_path / "split.pdf"
    build_split_sheet(path)
    faces = find_room_outlines(path, 0, SCALE)
    assert len(faces) == 3


def test_間取り全体の面が今の窓を通ってしまう(tmp_path) -> None:
    """**上限 200㎡ は、間取り全体を室として通す。**"""
    path = tmp_path / "whole.pdf"
    build_whole_floor_sheet(path)
    faces = find_room_outlines(path, 0, SCALE, max_sqm=200.0)
    assert len(faces) == 1
    assert faces[0].area_sqm > 30.0


def test_上限を下げると間取り全体の面が落ちる(tmp_path) -> None:
    path = tmp_path / "whole.pdf"
    build_whole_floor_sheet(path)
    assert find_room_outlines(path, 0, SCALE, max_sqm=30.0) == []


def test_上限を下げても室は残る(tmp_path) -> None:
    """**落とす代償が出ない上限がある**ことを、合成の紙で固定する。"""
    path = tmp_path / "split.pdf"
    build_split_sheet(path)
    faces = find_room_outlines(path, 0, SCALE, max_sqm=30.0)
    assert len(faces) == 3
    assert all(face.name is not None for face in faces)


def test_数え方の確かめが通る(tmp_path) -> None:
    assert check_definition(tmp_path)["通過"] is True
