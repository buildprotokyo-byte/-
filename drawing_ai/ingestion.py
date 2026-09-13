"""Load drawing files (PDF or raster image) and cut them into tiles.

Why tiling: a full architectural sheet at readable resolution is far beyond
what a compact open-source VLM can reliably read in one shot (small
dimension numerals and thin lines get lost when the whole page is
downscaled to fit the model's vision token budget). Human estimators don't
read a whole A1 sheet at a glance either -- they scan section by section.
So Phase 0 gets a small whole-page image (for classification/overview only),
while Phase 1-3 child agents each get one small, high-resolution, overlapping
tile plus the OCR text for that same tile region.

Overlap keeps a dimension line or symbol that straddles a tile boundary
readable by at least one tile.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import settings
from .schemas import Tile

Image.MAX_IMAGE_PIXELS = None  # architectural sheets are large; trust our own inputs


@dataclass
class RenderedSheet:
    sheet_id: str
    sheet_index: int
    source_name: str
    image_path: str
    width: int
    height: int


def _new_sheet_id(index: int) -> str:
    return f"sheet-{index:03d}-{uuid.uuid4().hex[:8]}"


def render_pdf(path: str | Path, out_dir: str | Path) -> list[RenderedSheet]:
    """Render every page of a PDF to a PNG at ``settings.render_dpi``.

    Imports PyMuPDF lazily so the rest of the package (schemas, orchestrator
    logic, tests) stays importable in environments that don't have it
    installed yet.
    """
    import fitz  # PyMuPDF

    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheets: list[RenderedSheet] = []
    zoom = settings.render_dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(path) as doc:
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            sheet_id = _new_sheet_id(index)
            out_path = out_dir / f"{sheet_id}.png"
            pix.save(str(out_path))
            sheets.append(
                RenderedSheet(
                    sheet_id=sheet_id,
                    sheet_index=index,
                    source_name=f"{path.name}#page={index + 1}",
                    image_path=str(out_path),
                    width=pix.width,
                    height=pix.height,
                )
            )
    return sheets


def load_image(path: str | Path, out_dir: str | Path, sheet_index: int = 0) -> RenderedSheet:
    """Register a single already-rasterized drawing (JPG/PNG/TIFF/...)."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(path) as im:
        im = im.convert("RGB")
        sheet_id = _new_sheet_id(sheet_index)
        out_path = out_dir / f"{sheet_id}.png"
        im.save(out_path, format="PNG")
        width, height = im.size

    return RenderedSheet(
        sheet_id=sheet_id,
        sheet_index=sheet_index,
        source_name=path.name,
        image_path=str(out_path),
        width=width,
        height=height,
    )


def load_drawing_set(paths: list[str | Path], out_dir: str | Path) -> list[RenderedSheet]:
    """Load a mixed set of PDFs/images into a flat, ordered list of sheets."""
    sheets: list[RenderedSheet] = []
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == ".pdf":
            sheets.extend(render_pdf(p, out_dir))
        else:
            sheets.append(load_image(p, out_dir, sheet_index=len(sheets)))
    return sheets


def make_overview_image(sheet: RenderedSheet, out_dir: str | Path, max_px: int = 1400) -> str:
    """Downscaled whole-sheet image for the Phase 0 'first glance' pass."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(sheet.image_path) as im:
        im = im.convert("RGB")
        scale = min(1.0, max_px / max(im.width, im.height))
        if scale < 1.0:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        out_path = Path(out_dir) / f"{sheet.sheet_id}-overview.png"
        im.save(out_path, format="PNG")
    return str(out_path)


def tile_sheet(sheet: RenderedSheet, out_dir: str | Path) -> list[Tile]:
    """Cut one rendered sheet into overlapping square-ish tiles.

    Tiles near the image edge are shrunk to fit rather than padded, so every
    tile still maps to a real image region.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    step = max(1, settings.tile_px - settings.tile_overlap_px)
    tiles: list[Tile] = []

    with Image.open(sheet.image_path) as im:
        im = im.convert("RGB")
        width, height = im.size

        rows = list(range(0, max(1, height - 1), step)) or [0]
        cols = list(range(0, max(1, width - 1), step)) or [0]
        if rows[-1] + settings.tile_px < height:
            rows.append(height - settings.tile_px if height > settings.tile_px else 0)
        if cols[-1] + settings.tile_px < width:
            cols.append(width - settings.tile_px if width > settings.tile_px else 0)

        seen: set[tuple[int, int]] = set()
        for r_idx, y0 in enumerate(rows):
            for c_idx, x0 in enumerate(cols):
                x0 = max(0, min(x0, width - 1))
                y0 = max(0, min(y0, height - 1))
                x1 = min(width, x0 + settings.tile_px)
                y1 = min(height, y0 + settings.tile_px)
                key = (x0, y0)
                if key in seen:
                    continue
                seen.add(key)

                crop = im.crop((x0, y0, x1, y1))
                tile_id = f"{sheet.sheet_id}-r{r_idx}c{c_idx}"
                tile_path = out_dir / f"{tile_id}.png"
                crop.save(tile_path, format="PNG")

                tiles.append(
                    Tile(
                        tile_id=tile_id,
                        sheet_id=sheet.sheet_id,
                        sheet_index=sheet.sheet_index,
                        row=r_idx,
                        col=c_idx,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        image_path=str(tile_path),
                    )
                )
    return tiles
