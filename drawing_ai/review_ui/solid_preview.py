"""3D-reconstruction review data: turns a SolidModel's wall segments (real
CAD vector geometry, not AI-inferred) plus sheet labels into the shape the
solid_preview.html template renders as a draggable 3D scene next to the
source 2D drawing.

Deliberately excludes anything not yet in SolidModel: window/door openings,
per-room floor polygons, and a "corridor" concept are not part of
solid_model_agent.build_solid_model's output today, so they are not
claimed here either -- see README 3.10.
"""
from __future__ import annotations

import re

from .. import vector_extractor as ve
from ..schemas import SolidModel

_BORING_TOKEN = re.compile(r"^[\d.,×WHDCH=]+$")


def build_solid_preview_data(gt: ve.SheetGroundTruth, model: SolidModel, sheet_id: str) -> dict:
    walls_out = [
        {"polygon_mm": w.polygon_mm, "height_mm": w.height_mm}
        for w in model.wall_segments
        if w.sheet_id == sheet_id
    ]

    labels = [
        {"text": w.text, "x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1}
        for w in gt.words
        if len(w.text) >= 2 and not _BORING_TOKEN.match(w.text)
    ]

    return {
        "walls": walls_out,
        "mm_per_px": gt.mm_per_px,
        "scale_text": gt.scale_text,
        "labels": labels,
    }
