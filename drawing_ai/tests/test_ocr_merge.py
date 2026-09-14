"""Tests for ocr_engine.merge_tile_words -- see its docstring and README 3.13
for the real bug this fixes: an un-merged multi-tile OCR pass double-reads
the overlap band between adjacent tiles, and the copy from whichever tile
crops through the middle of a word (not the word's own boundary) comes back
as short garbled noise even though the neighboring tile already read the
same word correctly in full.
"""
from __future__ import annotations

from drawing_ai.ocr_engine import merge_tile_words
from drawing_ai.schemas import Tile
from drawing_ai.vector_extractor import GroundTruthWord


def _tile(row, col, x0, y0, x1, y1) -> Tile:
    return Tile(
        tile_id=f"t-r{row}c{col}", sheet_id="s", sheet_index=0,
        row=row, col=col, x0=x0, y0=y0, x1=x1, y1=y1, image_path="unused.png",
    )


def test_merge_drops_the_clipped_duplicate_in_the_overlap_band():
    # two tiles side by side, 20px overlap (A: [0,100), B: [80,180)) --
    # core boundary is the midpoint, x=90.
    tile_a = _tile(0, 0, 0, 0, 100, 100)
    tile_b = _tile(0, 1, 80, 0, 180, 100)

    # a word truly centered at x=95 (B's core, since 95 >= 90): tile A's crop
    # cuts through it and OCR garbles it; tile B contains it whole and reads
    # it correctly.
    garbled_in_a = GroundTruthWord(text="X", x0=92, y0=40, x1=100, y1=60, confidence=0.1)
    correct_in_b = GroundTruthWord(text="正解", x0=85, y0=40, x1=105, y1=60, confidence=0.95)

    # a word fully inside tile A's own core (x=[0,90)), present only in A.
    solo_in_a = GroundTruthWord(text="abc", x0=20, y0=10, x1=40, y1=30, confidence=0.9)

    merged = merge_tile_words([
        (tile_a, [garbled_in_a, solo_in_a]),
        (tile_b, [correct_in_b]),
    ])

    texts = sorted(w.text for w in merged)
    assert texts == ["abc", "正解"], (
        "the clipped garbled duplicate ('X') must be dropped, the word solely "
        "in A's core must survive, and the correct full read from B must survive"
    )


def test_merge_keeps_words_at_the_true_sheet_edge():
    # a single tile with no neighbors on any side -- every word in it is
    # kept regardless of how close to the tile's own edge it sits, since
    # there is no neighboring tile that might contain a cleaner read.
    tile_a = _tile(0, 0, 0, 0, 100, 100)
    edge_word = GroundTruthWord(text="edge", x0=2, y0=2, x1=15, y1=15, confidence=0.8)

    merged = merge_tile_words([(tile_a, [edge_word])])
    assert [w.text for w in merged] == ["edge"]
