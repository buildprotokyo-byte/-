"""Shared text-grounding helpers used by every Phase 1-3 child agent.

Centralizes the "what's the best evidence we have for this tile, and how
much should it move our confidence" logic so Phase 1 (site facts), Phase 2
(intent), and Phase 3 (dimensions/symbols/elements) all treat vector
ground truth vs. OCR the same way, rather than each agent reinventing
(and potentially disagreeing on) the same trust hierarchy.

Trust hierarchy, highest first:
1. Vector ground truth (exact text pulled from a born-digital PDF's own
   text layer) -- agreement is near-certain confirmation, disagreement is
   a strong hallucination signal.
2. OCR -- lossy (see drawing_ai/README.md's real-sample-drawing findings:
   a lightweight open-source OCR engine missed roughly half of clearly
   legible dimension numbers on an actual architectural drawing), so it
   only gets a gentle nudge either way.
3. Nothing -- no adjustment; the VLM's own stated confidence stands alone.
"""
from __future__ import annotations

import re

from .schemas import Tile

_DIGITS_RE = re.compile(r"[0-9,.]+")


def normalize_number(text: str) -> str:
    return _DIGITS_RE.sub(lambda m: m.group(0).replace(",", ""), text)


def is_cad_native(tile: Tile) -> bool:
    """True only for a tile's ground truth that is genuinely CAD-exact
    text, not a searchable-PDF OCR layer sitting on a scanned image (see
    Tile.ground_truth_trust_tier). Confirmed on a real project (KDX802):
    a field-survey drawing was 78% one scanned image with an OCR text
    layer on top, and that layer was being treated as CAD-native-grade
    (verified_by_vector=True) despite containing visible OCR garbling --
    a false-confidence bug, not a rare edge case."""
    return tile.has_vector_ground_truth and tile.ground_truth_trust_tier == "cad_native"


def grounding_context(tile: Tile) -> str:
    """The best available text-grounding context for this tile, labeled by
    reliability so the model itself can weigh it appropriately."""
    if is_cad_native(tile):
        return f"[CAD原本から抽出した正確な文字列(信頼度高)] {tile.ground_truth_text}"
    if tile.has_vector_ground_truth:
        return f"[スキャン画像上のOCR文字レイヤー(OCRと同程度の信頼度)] {tile.ground_truth_text}"
    return tile.ocr_text


def numeric_adjustment(raw_text: str, tile: Tile) -> tuple[float, bool]:
    """Confidence delta + verified_by_vector for a numeric reading."""
    needle = normalize_number(raw_text)
    if not needle:
        return 0.0, False

    if is_cad_native(tile):
        haystack = normalize_number(tile.ground_truth_text)
        if needle in haystack:
            return 0.3, True
        return -0.5, False

    if tile.has_vector_ground_truth:
        # OCR-layer-over-scan: same trust tier as plain OCR, and never
        # marked verified_by_vector -- downstream logic (e.g. parent_agent
        # holding verified readings out of LLM reconciliation, or width/
        # depth area estimation requiring verification) must not treat
        # this as ground truth.
        haystack = normalize_number(tile.ground_truth_text)
        return (0.1, False) if needle in haystack else (-0.2, False)

    haystack = normalize_number(tile.ocr_text)
    return (0.1, False) if needle in haystack else (-0.2, False)


def text_adjustment(label: str, tile: Tile) -> tuple[float, bool]:
    """Confidence delta + verified_by_vector for a non-numeric label
    (room/element name, symbol glyph). Only rewards a match -- never
    penalizes an absence -- because OCR Japanese text and a model's own
    free-form phrasing are both too unreliable to safely treat "not found"
    as evidence of a wrong reading (see grounding.py module docstring)."""
    label = label.strip()
    if not label or not tile.has_vector_ground_truth:
        return 0.0, False
    if label not in tile.ground_truth_text:
        return 0.0, False
    return (0.25, True) if is_cad_native(tile) else (0.1, False)
