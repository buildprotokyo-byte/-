"""VTracer アダプタの回帰テスト。

ベクター化の「精度がどれだけ出るか」は benchmarks/run_vtracer_eval.py 側で測ります。
ここで固定するのは、SVG の解析・描き戻し・計測が壊れていないことです。
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from axes.image_axis.vtracer_vectorizer import (
    BW_PRESET,
    binarize_classical,
    measure_linework,
    parse_svg_paths,
    thin,
    vectorize,
)
from benchmarks.synthetic_plans import make_plan

# --- SVG の解析 -------------------------------------------------------------


def test_parse_simple_polygon() -> None:
    svg = '<svg><path d="M0,0 L10,0 L10,5 L0,5 Z " fill="#000000"/></svg>'
    paths = parse_svg_paths(svg)
    assert len(paths) == 1
    assert len(paths[0]) == 1
    ring = paths[0][0]
    assert ring[:, 0].max() == pytest.approx(10.0)
    assert ring[:, 1].max() == pytest.approx(5.0)


def test_translate_is_applied() -> None:
    svg = '<svg><path d="M0,0 L10,0 L10,5 L0,5 Z " transform="translate(100,200)"/></svg>'
    ring = parse_svg_paths(svg)[0][0]
    assert ring[:, 0].min() == pytest.approx(100.0)
    assert ring[:, 1].min() == pytest.approx(200.0)


def test_subpaths_stay_separate_so_holes_are_not_filled_in() -> None:
    """外周と穴が 1 つの <path> に入っていても、別々の ring として返すこと。"""
    svg = (
        '<svg><path d="M0,0 L20,0 L20,20 L0,20 Z M5,5 L5,15 L15,15 L15,5 Z "/></svg>'
    )
    paths = parse_svg_paths(svg)
    assert len(paths) == 1
    assert len(paths[0]) == 2


def test_cubic_bezier_is_flattened() -> None:
    svg = '<svg><path d="M0,0 C0,10 10,10 10,0 Z "/></svg>'
    ring = parse_svg_paths(svg)[0][0]
    # 分割数 8 + 始点 + Z の閉じ点
    assert len(ring) >= 9
    assert ring[:, 1].max() > 5.0


# --- 描き戻し ---------------------------------------------------------------


def test_hole_is_not_filled_in_the_rendered_mask() -> None:
    from axes.image_axis.vtracer_vectorizer import VectorDrawing

    svg = (
        '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="30" height="30">'
        '<path d="M0,0 L20,0 L20,20 L0,20 Z M5,5 L5,15 L15,15 L15,5 Z " fill="#000000"/>'
        "</svg>"
    )
    drawing = VectorDrawing(paths=parse_svg_paths(svg), width=30, height=30, svg=svg)
    mask = drawing.to_mask()
    assert mask[2, 2] > 0  # 外周の内側
    assert mask[10, 10] == 0  # 穴の中


def test_round_trip_keeps_the_drawing(tmp_path) -> None:
    """劣化なしの図面なら、ベクター化 → 描き戻しで元の画素とほぼ一致すること。"""
    plan = make_plan("clean")
    drawing = vectorize(plan.image)
    mask = drawing.to_mask() > 0
    original = plan.image < 128
    intersection = int((mask & original).sum())
    union = int((mask | original).sum())
    assert intersection / union > 0.90


def test_vectorize_uses_the_bw_preset_by_default() -> None:
    drawing = vectorize(make_plan("clean").image)
    assert drawing.params["colormode"] == BW_PRESET["colormode"] == "binary"
    assert drawing.path_count > 0
    assert drawing.vertex_count > drawing.path_count


def test_filter_speckle_reduces_the_number_of_paths() -> None:
    image = make_plan("heavy", seed=3).image
    few = vectorize(image, filter_speckle=16).path_count
    many = vectorize(image, filter_speckle=0).path_count
    assert few < many


# --- 細線化と計測 -----------------------------------------------------------


def test_thin_reduces_a_thick_line_to_one_pixel_wide() -> None:
    mask = np.zeros((40, 100), np.uint8)
    cv2.line(mask, (10, 20), (90, 20), 255, 7)
    skeleton = thin(mask)
    # 各列の芯線画素は 1 つだけ
    per_column = (skeleton[:, 20:80] > 0).sum(axis=0)
    assert set(np.unique(per_column)) <= {1}


def test_measure_linework_recovers_a_known_length() -> None:
    # 長さ 200 px・幅 5 px の帯(端の丸めが入らないよう矩形で作る)
    mask = np.zeros((60, 240), np.uint8)
    mask[28:33, 20:220] = 255
    measured = measure_linework(mask, mm_per_pixel=1.0)
    assert measured.total_length_mm == pytest.approx(200.0, abs=6.0)
    assert measured.component_count == 1
    assert measured.mean_stroke_width_px == pytest.approx(5.0, abs=1.0)


def test_measure_linework_counts_components() -> None:
    mask = np.zeros((60, 240), np.uint8)
    cv2.line(mask, (20, 20), (100, 20), 255, 3)
    cv2.line(mask, (140, 40), (220, 40), 255, 3)
    assert measure_linework(mask).component_count == 2


def test_region_restricts_only_the_length_total() -> None:
    mask = np.zeros((60, 240), np.uint8)
    cv2.line(mask, (0, 30), (239, 30), 255, 3)
    region = np.zeros_like(mask)
    region[:, :120] = 255
    whole = measure_linework(mask, mm_per_pixel=1.0)
    half = measure_linework(mask, mm_per_pixel=1.0, region=region)
    assert half.total_length_mm == pytest.approx(whole.total_length_mm / 2, rel=0.1)
    # 連結成分数は領域で切らない
    assert half.component_count == whole.component_count


# --- ベースライン -----------------------------------------------------------


def test_median_filter_erases_one_pixel_wide_linework() -> None:
    """3x3 メディアンは 1 画素幅の線を消す。ベースラインの限界として固定しておく。"""
    image = np.full((60, 240), 255, np.uint8)
    cv2.line(image, (20, 30), (220, 30), 0, 1)
    kept = binarize_classical(image, median_ksize=1)
    erased = binarize_classical(image, median_ksize=3)
    assert int((kept > 0).sum()) > 150
    assert int((erased > 0).sum()) == 0
