"""Assembles the "立体として組み立てる" (mentally stand the plan up into a
building) step from *verified* geometry rather than model guesswork.

This deliberately does less than a full BIM reconstruction: it only claims
what is directly traceable to ground truth --

- wall footprints: extracted directly from the PDF's filled vector paths
  (vector_extractor.wall_segments_mm), not inferred
- ceiling heights: exact "CH=nnnn" text tokens, also extracted directly
- the association between a wall/room and its height: a nearest-neighbor
  spatial match, which *is* a heuristic (flagged as such via
  ``match_distance_px`` on every ``RoomHeightFact`` so a reviewer can judge
  how much to trust a given match)

No VLM call is involved in this module at all -- it runs on the same
ground-truth data vector_extractor.py produces, once per sheet, and is
cheap enough to run unconditionally.
"""
from __future__ import annotations

import logging
import re

from .. import vector_extractor as ve
from ..schemas import ElementReading, RoomHeightFact, SolidModel, Tile

logger = logging.getLogger("drawing_ai.agents.solid_model")

_CH_PATTERN = re.compile(r"CH[=＝]\s*([0-9]{3,5})")

# Beyond this pixel distance a CH= label is unlikely to actually belong to
# the room/wall it's being matched to (rough calibration: on the real
# sample sheet, at ~220 DPI, in-room CH labels sat within a few hundred
# pixels of the room's own text/geometry; this is intentionally generous
# rather than tight, since match_distance_px is reported for every match so
# a reviewer -- human or a later pipeline stage -- can apply a tighter cut.
_MAX_MATCH_DISTANCE_PX = 1400.0


def _ch_tokens(gt: ve.SheetGroundTruth) -> list[tuple[float, float, float]]:
    """Every (cx, cy, ceiling_height_mm) found on one sheet."""
    out = []
    for w in gt.words:
        m = _CH_PATTERN.search(w.text)
        if m:
            out.append((w.cx, w.cy, float(m.group(1))))
    return out


def build_solid_model(
    ground_truths: dict[str, ve.SheetGroundTruth],
    elements: list[ElementReading],
    tiles_by_id: dict[str, Tile],
) -> SolidModel:
    model = SolidModel()

    for sheet_id, gt in ground_truths.items():
        if gt.mm_per_px is not None:
            model.sheet_scale_mm_per_px[sheet_id] = gt.mm_per_px

        ch_tokens = _ch_tokens(gt)
        walls = ve.wall_segments_mm(gt)

        for wall in walls:
            if not ch_tokens or gt.mm_per_px is None:
                model.wall_segments.append(wall)
                continue
            cx = sum(p[0] for p in wall.polygon_mm) / len(wall.polygon_mm) / gt.mm_per_px
            cy = sum(p[1] for p in wall.polygon_mm) / len(wall.polygon_mm) / gt.mm_per_px
            best = min(ch_tokens, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
            dist = ((best[0] - cx) ** 2 + (best[1] - cy) ** 2) ** 0.5
            if dist <= _MAX_MATCH_DISTANCE_PX:
                wall.height_mm = best[2]
                wall.height_source = f"CH={best[2]:.0f} (最近傍ラベル, 距離{dist:.0f}px)"
            model.wall_segments.append(wall)

        if not ch_tokens:
            continue

        for el in elements:
            if el.element_type != "room" or el.sheet_id != sheet_id:
                continue
            pos = _element_position_px(el, tiles_by_id)
            if pos is None:
                continue
            ex, ey = pos
            best = min(ch_tokens, key=lambda t: (t[0] - ex) ** 2 + (t[1] - ey) ** 2)
            dist = ((best[0] - ex) ** 2 + (best[1] - ey) ** 2) ** 0.5
            if dist > _MAX_MATCH_DISTANCE_PX:
                continue
            confidence = max(0.3, 1.0 - dist / _MAX_MATCH_DISTANCE_PX)
            model.room_heights.append(
                RoomHeightFact(
                    sheet_id=sheet_id,
                    room_label=el.label_ja,
                    ceiling_height_mm=best[2],
                    match_distance_px=dist,
                    confidence=confidence,
                )
            )

    if not model.wall_segments:
        model.warnings.append(
            "壁のベクター形状が抽出できませんでした(スキャン画像図面か、このCADベンダー向けの"
            "塗りつぶし色設定が現在の想定と異なる可能性があります)。"
        )

    return model


def _element_position_px(el: ElementReading, tiles_by_id: dict[str, Tile]) -> tuple[float, float] | None:
    tiles = [tiles_by_id[tid] for tid in el.tile_ids if tid in tiles_by_id]
    if not tiles:
        return None
    cx = sum((t.x0 + t.x1) / 2 for t in tiles) / len(tiles)
    cy = sum((t.y0 + t.y1) / 2 for t in tiles) / len(tiles)
    return cx, cy
