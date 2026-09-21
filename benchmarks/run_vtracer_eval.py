"""ステップ2: VTracer のパラメータ探索と、画像軸の読み取り精度への効果測定。

実験の組み方
------------
下流の読み取りアルゴリズム(細線化 → 線長・連結成分数の計測)を固定し、**前処理
だけを差し替えて**比較します。こうすると「VTracer を挟んだことによる差」だけが出ます。

比較する 3 つのパイプライン:

1. ``classical``      古典的画像処理のみ。メディアンフィルタ(1/3/5)+ 大津の二値化
2. ``vtracer``        グレースケール画像をそのまま VTracer に入れる
3. ``otsu+vtracer``   大津の二値化で二値にしてから VTracer に入れる

``classical`` はメディアンのカーネルを振って**最も良かったものをベースラインの成績
とします**(ベースラインを不当に弱くしないため)。

評価指標
--------
劣化した図面では「壁のような太い線」と「記号のような細い線」で壊れ方がまったく違う
ため、線長を 2 つに分けて測ります。

1. 壁部の線長   … 記号の正解ボックスの外側にある線(正解 2596 px)
2. 記号部の線長 … 開き戸の円弧・戸の板・窓の細線(正解 1162 px)
3. 連結成分数   … ノイズ由来の偽の図形がいくつ生き残ったか(劣化なしでは 13)

実行: ``python -m benchmarks.run_vtracer_eval``
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from axes.image_axis.vtracer_vectorizer import (
    binarize_classical,
    measure_linework,
    vectorize,
)
from benchmarks.synthetic_plans import ALL_LEVELS, DegradationLevel, SyntheticPlan, make_plan

_OUTER_W, _OUTER_H = 640, 400
_DOOR_SPAN, _WINDOW_SPAN = 50, 81

#: 記号の正解ボックスをこれだけ広げた領域を「記号部」とみなす。
#: medium / heavy では図面がわずかに回転するため、その分の余裕。
SYMBOL_REGION_MARGIN = 10


def ground_truth_wall_px() -> float:
    """壁の芯線の総延長(開口部を除く)。"""
    return float(
        (_OUTER_W - 2 * _WINDOW_SPAN)  # 上外壁(窓 2)
        + (_OUTER_W - _WINDOW_SPAN)  # 下外壁(窓 1)
        + (_OUTER_H - _DOOR_SPAN)  # 左外壁(戸 1)
        + (_OUTER_H - _WINDOW_SPAN)  # 右外壁(窓 1)
        + (_OUTER_H - 2 * _DOOR_SPAN)  # 縦の間仕切り(戸 2)
        + (_OUTER_W - _DOOR_SPAN)  # 横の間仕切り(戸 1)
    )


def ground_truth_symbol_px() -> float:
    """記号の線の総延長(開き戸 = 戸の板 + 1/4 円弧、窓 = 細線 2 本)。"""
    doors = 4 * (_DOOR_SPAN + math.pi / 2 * _DOOR_SPAN)
    windows = 4 * (2 * _WINDOW_SPAN)
    return float(doors + windows)


GT_WALL_PX = ground_truth_wall_px()
GT_SYMBOL_PX = ground_truth_symbol_px()
GT_COMPONENT_COUNT = 13
"""劣化のない図面で数えられる、独立した黒の連結成分の数。"""


def symbol_region(plan: SyntheticPlan) -> np.ndarray:
    """記号の正解ボックスを塗った領域マスク。"""
    mask = np.zeros(plan.image.shape[:2], np.uint8)
    for symbol in plan.symbols:
        x1, y1, x2, y2 = symbol.box
        cv2.rectangle(
            mask,
            (x1 - SYMBOL_REGION_MARGIN, y1 - SYMBOL_REGION_MARGIN),
            (x2 + SYMBOL_REGION_MARGIN, y2 + SYMBOL_REGION_MARGIN),
            255,
            -1,
        )
    return mask


@dataclass
class Result:
    level: DegradationLevel
    pipeline: str
    detail: str
    wall_px: float
    symbol_px: float
    component_count: int

    @property
    def wall_error_pct(self) -> float:
        return (self.wall_px - GT_WALL_PX) / GT_WALL_PX * 100.0

    @property
    def symbol_error_pct(self) -> float:
        return (self.symbol_px - GT_SYMBOL_PX) / GT_SYMBOL_PX * 100.0

    @property
    def combined_error_pct(self) -> float:
        """壁部と記号部の誤差の絶対値の平均。パラメータ選択に使う。"""
        return (abs(self.wall_error_pct) + abs(self.symbol_error_pct)) / 2.0


def _measure(
    level: DegradationLevel,
    pipeline: str,
    detail: str,
    mask: np.ndarray,
    symbols: np.ndarray,
) -> Result:
    inside = measure_linework(mask, region=symbols)
    outside = measure_linework(mask, region=cv2.bitwise_not(symbols))
    return Result(
        level=level,
        pipeline=pipeline,
        detail=detail,
        wall_px=outside.total_length_mm / 10.0,
        symbol_px=inside.total_length_mm / 10.0,
        component_count=inside.component_count,  # 全体で数えた値(領域に依存しない)
    )


FILTER_SPECKLE_SWEEP = (0, 2, 4, 8, 16)


def evaluate_level(level: DegradationLevel, seed: int) -> list[Result]:
    plan = make_plan(level, seed=seed)
    symbols = symbol_region(plan)
    results: list[Result] = []

    for ksize in (1, 3, 5):
        results.append(
            _measure(
                level,
                "classical",
                f"median={ksize}",
                binarize_classical(plan.image, ksize),
                symbols,
            )
        )

    for filter_speckle in FILTER_SPECKLE_SWEEP:
        drawing = vectorize(plan.image, filter_speckle=filter_speckle)
        results.append(
            _measure(level, "vtracer", f"fs={filter_speckle}", drawing.to_mask(), symbols)
        )

    otsu_ink = cv2.bitwise_not(binarize_classical(plan.image, 1))
    for filter_speckle in FILTER_SPECKLE_SWEEP:
        drawing = vectorize(otsu_ink, filter_speckle=filter_speckle)
        results.append(
            _measure(level, "otsu+vtracer", f"fs={filter_speckle}", drawing.to_mask(), symbols)
        )

    return results


def _best(results: list[Result], pipeline: str) -> Result:
    return min(
        (r for r in results if r.pipeline == pipeline), key=lambda r: r.combined_error_pct
    )


def main(seed: int = 7) -> dict[str, Any]:
    print(
        f"正解値: 壁部 {GT_WALL_PX:.0f} px / 記号部 {GT_SYMBOL_PX:.0f} px / "
        f"連結成分 {GT_COMPONENT_COUNT} 個\n"
    )

    summary: dict[str, Any] = {}
    header = f"{'劣化':<7}{'パイプライン':<14}{'設定':<11}{'壁部':>9}{'記号部':>10}{'成分数':>8}"
    for level in ALL_LEVELS:
        results = evaluate_level(level, seed)
        print(header)
        print("-" * 72)
        for r in results:
            print(
                f"{r.level:<7}{r.pipeline:<14}{r.detail:<11}"
                f"{r.wall_error_pct:>+8.1f}%{r.symbol_error_pct:>+9.1f}%{r.component_count:>8}"
            )
        print()
        summary[level] = {name: _best(results, name) for name in
                          ("classical", "vtracer", "otsu+vtracer")}

    print("=== 各劣化レベルでの最良同士の比較 ===")
    print(f"{'劣化':<8}{'パイプライン':<14}{'設定':<11}{'壁部':>9}{'記号部':>10}{'成分数':>8}")
    print("-" * 62)
    for level in ALL_LEVELS:
        for name, r in summary[level].items():
            print(
                f"{level:<8}{name:<14}{r.detail:<11}"
                f"{r.wall_error_pct:>+8.1f}%{r.symbol_error_pct:>+9.1f}%{r.component_count:>8}"
            )
        print()
    return summary


if __name__ == "__main__":
    main()
