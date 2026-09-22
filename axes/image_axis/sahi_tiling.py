"""画像軸: SAHI方式(タイル分割推論)によるGrounding DINOの検出改善を試すラッパー。

背景
----
段階Aの実測(`docs/stage_a_report.md`、PR #1 commit `c8e4e81`)で、Grounding DINOの
最高スコア検出が**図面全体を囲む矩形**になり、個々の記号の位置とは無関係であることが
分かった。スコアが高い検出ほど不正解という、しきい値の調整では直らない逆相関だった。

SAHI(Slicing Aided Hyper Inference)は、この種の「小さな対象 / 大きな背景」問題に
対する一般的な対策で、画像を重なりを持つタイルに分割し、タイルごとに検出してから
元の座標系へ戻す。**モデルに一度に見せる画像を小さくすることで、「画像全体を囲む」
という選択肢自体を与えない**狙いがある。

このモジュールは既存の ``DetectionBackend`` プロトコル(`grounding_dino_adapter.py`)
をそのまま満たす ``SlicedInferenceBackend`` を提供する。既存の ``GroundingDinoAdapter``
やしきい値ロジックには一切手を加えず、バックエンドを差し替えるだけで使える。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from axes.image_axis.grounding_dino_adapter import DetectionBackend, RawDetection

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class TilingConfig:
    """タイル分割のパラメータ。"""

    tile_size: int = 256
    """タイル1辺の画素数。図面の全体サイズ(760x520)よりずっと小さくし、
    「画像全体を囲む」検出が原理的に出せないようにする。"""

    overlap: float = 0.25
    """隣接タイルの重なり率(0〜1未満)。記号がタイルの境界で分断されて
    見切れることに対する保険。"""

    nms_iou_threshold: float = 0.5
    """タイル間で重複した検出を1件にまとめるためのIoUしきい値。"""


def _tile_starts(total: int, tile: int, overlap: float) -> list[int]:
    """0から`total`までを、`tile`幅・`overlap`率で覆うタイルの開始位置一覧を返す。

    最後のタイルは右端/下端に揃え、必ず全域を覆う(端が半端に余らない)。
    """
    if total <= tile:
        return [0]
    stride = max(1, int(round(tile * (1.0 - overlap))))
    starts = list(range(0, total - tile + 1, stride))
    last = total - tile
    if starts[-1] != last:
        starts.append(last)
    return starts


def make_tiles(height: int, width: int, config: TilingConfig) -> list[Box]:
    """画像全体を覆うタイル境界 ``(x1, y1, x2, y2)`` の一覧を返す。"""
    xs = _tile_starts(width, config.tile_size, config.overlap)
    ys = _tile_starts(height, config.tile_size, config.overlap)
    tiles = []
    for y in ys:
        for x in xs:
            x2 = min(x + config.tile_size, width)
            y2 = min(y + config.tile_size, height)
            tiles.append((x, y, x2, y2))
    return tiles


def _iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _greedy_nms(
    detections: list[RawDetection], iou_threshold: float
) -> list[RawDetection]:
    """スコアの高い順に貪欲選択し、IoUが閾値を超える重複を捨てる。"""
    ordered = sorted(
        detections, key=lambda d: min(d.box_score, d.text_score), reverse=True
    )
    kept: list[RawDetection] = []
    for detection in ordered:
        if all(_iou(detection.box, k.box) < iou_threshold for k in kept):
            kept.append(detection)
    return kept


class SlicedInferenceBackend:
    """既存の ``DetectionBackend`` をタイル分割で包む(SAHI方式)。

    ``base`` に渡したバックエンドの ``detect()`` を、画像全体ではなく個々の
    タイルに対して呼び出し、返ってきた検出ボックスをタイルのオフセット分だけ
    ずらして元の座標系に戻す。タイル間で重複した検出は ``_greedy_nms`` で
    1件にまとめる。
    """

    def __init__(self, base: DetectionBackend, config: TilingConfig = TilingConfig()) -> None:
        self.base = base
        self.config = config

    def detect(self, image: np.ndarray, prompt: str) -> list[RawDetection]:
        height, width = image.shape[:2]
        tiles = make_tiles(height, width, self.config)
        merged: list[RawDetection] = []
        for x1, y1, x2, y2 in tiles:
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            for detection in self.base.detect(crop, prompt):
                bx1, by1, bx2, by2 = detection.box
                global_box = (bx1 + x1, by1 + y1, bx2 + x1, by2 + y1)
                merged.append(
                    RawDetection(global_box, detection.box_score, detection.text_score, detection.label)
                )
        return _greedy_nms(merged, self.config.nms_iou_threshold)
