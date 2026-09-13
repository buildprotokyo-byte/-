from __future__ import annotations

from PIL import Image

from drawing_ai import config
from drawing_ai.ingestion import RenderedSheet, tile_sheet


def test_tile_sheet_covers_whole_image(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "tile_px", 100)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 20)

    img_path = tmp_path / "sheet.png"
    Image.new("RGB", (250, 180), color=(255, 255, 255)).save(img_path)

    sheet = RenderedSheet(
        sheet_id="sheet-000-test",
        sheet_index=0,
        source_name="sheet.png",
        image_path=str(img_path),
        width=250,
        height=180,
    )

    tiles = tile_sheet(sheet, tmp_path / "tiles")

    assert len(tiles) > 1, "a 250x180 image with 100px tiles should produce multiple tiles"

    # every pixel of the source image must be covered by at least one tile
    covered = [[False] * 250 for _ in range(180)]
    for t in tiles:
        for y in range(t.y0, t.y1):
            for x in range(t.x0, t.x1):
                covered[y][x] = True
    assert all(all(row) for row in covered), "tiling left a gap uncovered by any tile"

    # neighboring tiles in the same row should overlap by roughly the
    # configured overlap amount, not touch edge-to-edge
    row0 = sorted([t for t in tiles if t.row == 0], key=lambda t: t.x0)
    for a, b in zip(row0, row0[1:]):
        assert b.x0 < a.x1, "adjacent tiles should overlap"


def test_tile_sheet_tiles_are_valid_images(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "tile_px", 64)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 8)

    img_path = tmp_path / "sheet.png"
    Image.new("RGB", (140, 90), color=(10, 20, 30)).save(img_path)
    sheet = RenderedSheet(
        sheet_id="sheet-001-test",
        sheet_index=0,
        source_name="sheet.png",
        image_path=str(img_path),
        width=140,
        height=90,
    )

    tiles = tile_sheet(sheet, tmp_path / "tiles")
    assert tiles
    for t in tiles:
        with Image.open(t.image_path) as im:
            assert im.width == t.x1 - t.x0
            assert im.height == t.y1 - t.y0
