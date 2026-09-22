"""SAHI方式タイル分割(axes/image_axis/sahi_tiling.py)の単体テスト。"""

from __future__ import annotations

import numpy as np

from axes.image_axis.grounding_dino_adapter import RawDetection
from axes.image_axis.sahi_tiling import (
    SlicedInferenceBackend,
    TilingConfig,
    _greedy_nms,
    make_tiles,
)


def test_make_tiles_covers_the_whole_image_without_gaps() -> None:
    height, width = 520, 760
    config = TilingConfig(tile_size=256, overlap=0.25)
    tiles = make_tiles(height, width, config)

    covered = np.zeros((height, width), dtype=bool)
    for x1, y1, x2, y2 in tiles:
        covered[y1:y2, x1:x2] = True
    assert covered.all()


def test_make_tiles_last_tile_is_flush_with_the_edge() -> None:
    tiles = make_tiles(520, 760, TilingConfig(tile_size=256, overlap=0.25))
    max_x2 = max(t[2] for t in tiles)
    max_y2 = max(t[3] for t in tiles)
    assert max_x2 == 760
    assert max_y2 == 520


def test_make_tiles_single_tile_when_smaller_than_tile_size() -> None:
    tiles = make_tiles(100, 100, TilingConfig(tile_size=256, overlap=0.25))
    assert tiles == [(0, 0, 100, 100)]


def test_greedy_nms_keeps_highest_scoring_of_overlapping_pair() -> None:
    low = RawDetection((0, 0, 10, 10), box_score=0.5, text_score=0.5, label="door")
    high = RawDetection((1, 1, 11, 11), box_score=0.9, text_score=0.9, label="door")
    kept = _greedy_nms([low, high], iou_threshold=0.5)
    assert kept == [high]


def test_greedy_nms_keeps_both_when_disjoint() -> None:
    left = RawDetection((0, 0, 10, 10), box_score=0.9, text_score=0.9, label="door")
    right = RawDetection((100, 100, 110, 110), box_score=0.9, text_score=0.9, label="door")
    kept = _greedy_nms([left, right], iou_threshold=0.5)
    assert set(kept) == {left, right}


class _RecordingBackend:
    """タイルごとに呼ばれた回数とcropの形を記録するテスト用バックエンド。"""

    def __init__(self, responses: dict[tuple[int, int], list[RawDetection]]) -> None:
        self._responses = responses
        self.calls: list[tuple[int, int]] = []

    def detect(self, image: np.ndarray, prompt: str) -> list[RawDetection]:
        shape = image.shape[:2]
        self.calls.append(shape)
        return list(self._responses.get(shape, []))


def test_sliced_backend_offsets_boxes_back_to_global_coordinates() -> None:
    image = np.zeros((520, 760), dtype=np.uint8)
    config = TilingConfig(tile_size=256, overlap=0.25)
    tiles = make_tiles(520, 760, config)
    # 2番目のタイル(x1>0)の中に、ローカル座標(10,10,20,20)の検出を仕込む。
    target_tile = tiles[1]
    tile_shape = (target_tile[3] - target_tile[1], target_tile[2] - target_tile[0])
    backend = _RecordingBackend(
        {tile_shape: [RawDetection((10, 10, 20, 20), 0.9, 0.9, "door")]}
    )
    sliced = SlicedInferenceBackend(backend, config)

    detections = sliced.detect(image, "a door symbol")

    assert len(backend.calls) == len(tiles)
    expected_box = (
        10 + target_tile[0],
        10 + target_tile[1],
        20 + target_tile[0],
        20 + target_tile[1],
    )
    assert any(d.box == expected_box for d in detections)


def test_sliced_backend_deduplicates_overlapping_tile_detections() -> None:
    """タイル0とタイル1の重なり域(x=192〜256)に印を置き、両方のタイルが
    同じ物体を検出して同じ大域座標を返す状況を作る。NMSで1件にまとまるはず。
    """
    config = TilingConfig(tile_size=256, overlap=0.25)
    image = np.zeros((520, 760), dtype=np.uint8)
    marker_x, marker_y = 220, 100
    image[marker_y, marker_x] = 255

    class MarkerSeekingBackend:
        """crop内の非ゼロ画素の位置を中心に、小さな箱を返す(印が無ければ検出なし)。"""

        def detect(self, image: np.ndarray, prompt: str) -> list[RawDetection]:
            ys, xs = np.nonzero(image)
            if len(xs) == 0:
                return []
            x, y = int(xs[0]), int(ys[0])
            return [RawDetection((x - 2, y - 2, x + 2, y + 2), 0.9, 0.9, "door")]

    sliced = SlicedInferenceBackend(MarkerSeekingBackend(), config)
    detections = sliced.detect(image, "a door symbol")

    assert len(detections) == 1
    x1, y1, x2, y2 = detections[0].box
    assert x1 < marker_x < x2
    assert y1 < marker_y < y2
