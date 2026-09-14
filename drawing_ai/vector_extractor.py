"""Deterministic ground-truth extraction from born-digital (vector) PDF pages.

This is the core of the accuracy upgrade: a CAD-exported PDF (as opposed to a
scanned image) embeds its dimension numbers, room labels, and annotation
text as literal text objects with exact coordinates -- not pixels a model
has to transcribe. Where this data is available, it is used as a witness
the AI-based readings (OCR + VLM) are checked against, instead of asking the
AI to "read" something that is already known exactly.

Three things are extracted per sheet, all with zero model calls:

1. **Text layer** (``GroundTruthWord``): every text run with its exact
   string and pixel-space bounding box. When a tile overlaps one of these,
   the orchestrator hands the tile that exact text instead of (or alongside)
   OCR output, so a VLM reading of "2,730" next to authoritative text
   "2730" is either confirmed or flagged, never silently trusted alone.

2. **Wall footprints** (``WallSegmentGeometry``): this drawing set (and,
   empirically, this CAD vendor's export convention generally) draws walls
   as solid-filled polygons in a consistent gray. Those fills are extracted
   directly as wall geometry -- not inferred by a model -- and are the input
   to the Phase 3 "solid model" (see solid_model_agent.py).

3. **Dimension-chain arithmetic** (``DimensionChainFlag``): dimension
   numbers that sit in a straight run (a human reads these and mentally
   adds them up to sanity-check against the overall dimension) are
   clustered by position and cross-checked against other clusters on the
   same axis, exactly the way a human estimator re-adds a dimension string
   with a calculator. This needs no line-to-number geometric matching (which
   turned out, on a real sample drawing, to be unreliable to do generically
   across CAD export conventions -- see the module docstring trade-off
   note below) -- only text positions, which are exact.

Trade-off note: an earlier design for this module also tried to verify each
dimension number against the *measured pixel length of its own dimension
line*, using the sheet's print scale. On a real sample drawing this proved
unreliable to implement generically (dimension lines, extension lines, and
unrelated fixture linework share colors and are not consistently
distinguishable without per-vendor tuning), and a flaky geometric check
would hurt trust rather than build it. That approach is not implemented
here; the scale factor computed below is used only to convert wall
geometry to real-world millimeters for the solid model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .config import settings
from .ingestion import RenderedSheet
from .schemas import DimensionChainFlag, WallSegmentGeometry

logger = logging.getLogger("drawing_ai.vector_extractor")

# This CAD export's wall fill color, empirically identified from the sample
# drawing (a mid-gray, consistent across all sheets). Extraction still works
# without a scale if this doesn't match another vendor's convention -- wall
# geometry then simply comes back empty, which orchestrator/solid_model
# handle gracefully -- but it means "the wall geometry layer is tuned to one
# observed export convention and should be recalibrated per CAD vendor."
_WALL_FILL_COLOR = (0.494, 0.463, 0.447)
_WALL_FILL_TOLERANCE = 0.03

_SCALE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_NUMERIC_PATTERN = re.compile(r"^[0-9][0-9,]*$")

# Dimension-chain clustering tolerances, in PDF points (not yet converted to
# pixels -- these operate directly on page-space word coordinates).
_CHAIN_BAND_TOLERANCE_PT = 3.0
_CHAIN_MIN_MEMBERS = 3
_CHAIN_MATCH_TOLERANCE_MM = 3.0
# Empirically calibrated against a real sample drawing: consecutive labels
# within one genuine dimension chain were <=251pt apart; an unrelated
# dimension sharing the same baseline sat 596pt away. 350pt sits between
# the two with margin on both sides.
_CHAIN_MAX_GAP_PT = 350.0
# How far beyond a chain's own span (along the primary axis) a candidate
# "total" may sit and still be considered spatially associated with that
# chain, rather than an unrelated number elsewhere on the sheet.
_CHAIN_TOTAL_SPAN_MARGIN_PT = 60.0

# Row-grouping tolerance for extract_table_rows(), in PDF points. Wider than
# _CHAIN_BAND_TOLERANCE_PT (which targets same-baseline dimension digits)
# because a spec-sheet table row's cells are not always perfectly
# baseline-aligned (merged cells, differing font sizes between a label and
# its value) -- this is a separate, independently-tuned constant rather than
# reusing the dimension-chain one, since the two serve different purposes.
_TABLE_ROW_TOLERANCE_PT = 9.0


@dataclass
class GroundTruthWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def is_vertical(self) -> bool:
        return (self.y1 - self.y0) > (self.x1 - self.x0) * 1.8


@dataclass
class SheetGroundTruth:
    sheet_id: str
    words: list[GroundTruthWord] = field(default_factory=list)
    wall_polygons_px: list[list[tuple[float, float]]] = field(default_factory=list)
    mm_per_px: float | None = None
    scale_text: str | None = None
    # "cad_native" | "ocr_layer_over_scan" -- see _has_full_page_scan_image.
    # A PDF's own text layer is not automatically CAD-exact: a scanned
    # drawing run through OCR-to-searchable-PDF software also has a real
    # text layer that get_text("words") happily returns, but it carries
    # the same unreliability as OCR, not CAD-native exactness. Confirmed
    # on a real project (KDX802's field-survey drawing): 78% of the page
    # was one scanned image with a text layer on top, and that "vector"
    # text contained visible OCR garbling ("ワ子", "江け" mixed into
    # otherwise-numeric dimension strings) while still being treated as
    # verified_by_vector-grade -- a false-confidence bug, not a rare edge
    # case. Wall-fill polygon extraction (wall_segments_mm) is unaffected:
    # it reads actual vector graphics (get_drawings()), which a raster
    # scan doesn't have regardless of any OCR text layer on top.
    text_trust_tier: str = "cad_native"


def _has_full_page_scan_image(page, threshold: float = 0.7) -> bool:
    """True if a single embedded image covers most of the page -- the
    signature of a scanned drawing (as opposed to a small logo/stamp
    image sitting alongside real vector content, which does not by
    itself make the page's text layer OCR-derived)."""
    try:
        page_area = abs(page.rect.width * page.rect.height)
    except Exception:  # noqa: BLE001
        return False
    if page_area <= 0:
        return False
    for img in page.get_images(full=True):
        try:
            bbox = page.get_image_bbox(img)
        except Exception:  # noqa: BLE001
            continue
        if abs(bbox.width * bbox.height) / page_area >= threshold:
            return True
    return False


def is_vector_native(sheet: RenderedSheet) -> bool:
    """Cheap check: does this sheet have a real PDF text/vector layer?

    Note: "has a text layer" (checked here) and "that text layer is
    CAD-exact" (see SheetGroundTruth.text_trust_tier, set in
    extract_ground_truth) are different questions -- a page can pass this
    check and still only warrant OCR-grade trust.
    """
    if not sheet.source_pdf_path or sheet.source_pdf_page_index is None:
        return False
    try:
        import fitz

        with fitz.open(sheet.source_pdf_path) as doc:
            page = doc[sheet.source_pdf_page_index]
            has_text = len(page.get_text("words")) > 0
            # A page that is really just a full-page scanned image embedded
            # in the PDF (no real text layer) would show 0 words above; a
            # page with a small logo/stamp image alongside real vector
            # content is still vector-native for our purposes, so we only
            # gate on the text-layer check.
            return has_text
    except Exception as exc:  # noqa: BLE001 - any parse failure means "not usable"
        logger.info("vector-native check failed for %s: %s", sheet.sheet_id, exc)
        return False


def _pdf_to_px(x: float, y: float, zoom: float) -> tuple[float, float]:
    return x * zoom, y * zoom


def extract_ground_truth(sheet: RenderedSheet) -> SheetGroundTruth | None:
    """Pull the text layer, wall fills, and scale factor for one sheet.

    Returns ``None`` if the sheet isn't backed by a vector PDF page (e.g. it
    came from a JPG/PNG, or from a DWG/DXF/JWW that had to be rasterized
    upstream before reaching this pipeline) -- callers fall back to OCR only.
    """
    if not is_vector_native(sheet):
        return None

    import fitz

    zoom = settings.render_dpi / 72.0
    gt = SheetGroundTruth(sheet_id=sheet.sheet_id)

    with fitz.open(sheet.source_pdf_path) as doc:
        page = doc[sheet.source_pdf_page_index]
        gt.text_trust_tier = "ocr_layer_over_scan" if _has_full_page_scan_image(page) else "cad_native"

        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            px0, py0 = _pdf_to_px(x0, y0, zoom)
            px1, py1 = _pdf_to_px(x1, y1, zoom)
            gt.words.append(GroundTruthWord(text=text, x0=px0, y0=py0, x1=px1, y1=py1))

        gt.scale_text, denom = _find_scale(gt.words)
        if denom:
            # real_mm = render_px * 25.4 * denom / render_dpi (see module
            # docstring derivation: 1 printed unit at 1/denom scale
            # represents `denom` real-world units, and render_dpi pixels
            # cover one inch of the printed page).
            gt.mm_per_px = 25.4 * denom / settings.render_dpi

        try:
            for d in page.get_drawings():
                if not _is_wall_fill(d):
                    continue
                poly = _drawing_to_polygon(d)
                if poly and len(poly) >= 3:
                    gt.wall_polygons_px.append([_pdf_to_px(x, y, zoom) for x, y in poly])
        except Exception as exc:  # noqa: BLE001
            logger.info("wall polygon extraction failed for %s: %s", sheet.sheet_id, exc)

    return gt


def _find_scale(words: list[GroundTruthWord]) -> tuple[str | None, int | None]:
    """Find a "N/M" (e.g. "1/50") scale token, preferring one positioned
    near a "縮尺" (scale) label if present, else the only such token found.

    A site plan commonly has *other* legitimate "N/M"-shaped tokens that
    are not the drawing scale at all -- a ramp/slope gradient (e.g.
    "1/12") is a standard site-plan annotation and matches the same
    pattern. Without a "縮尺" label to anchor the distance sort, picking
    "the first token found" risked silently grabbing a slope ratio instead
    of the real scale (and every downstream mm-per-px conversion would
    then be wrong without any signal that it happened). When multiple
    candidates exist and none can be anchored to a label, this refuses to
    guess rather than silently pick one -- the same principle used
    elsewhere in this module (see the wall-fill trade-off note) for not
    publishing an unreliable geometric check in place of no check at all.
    """
    scale_words = [w for w in words if _SCALE_PATTERN.match(w.text)]
    if not scale_words:
        return None, None

    label = next((w for w in words if "縮尺" in w.text or "縮 尺" in w.text), None)
    if label:
        scale_words.sort(key=lambda w: (w.cx - label.cx) ** 2 + (w.cy - label.cy) ** 2)
    elif len(scale_words) > 1:
        return None, None

    chosen = scale_words[0]
    m = _SCALE_PATTERN.match(chosen.text)
    denom = int(m.group(2))
    return chosen.text.strip(), denom


def _is_wall_fill(drawing: dict) -> bool:
    if drawing.get("type") not in ("f", "fs"):
        return False
    color = drawing.get("fill")
    if not color or len(color) != 3:
        return False
    return all(abs(c - t) <= _WALL_FILL_TOLERANCE for c, t in zip(color, _WALL_FILL_COLOR))


def _drawing_to_polygon(drawing: dict) -> list[tuple[float, float]] | None:
    """Walk one drawing's items (a sequence of connected 'l'/'qu'/'re'
    segments forming one closed fill) into a single ordered vertex list.

    A single ``get_drawings()`` entry is one filled path, but its ``items``
    describe it edge-by-edge (typically several 'l' segments, sometimes a
    single 're'/'qu'); the vertices must be taken in order and deduplicated
    at the seams, not treated as separate shapes.
    """
    points: list[tuple[float, float]] = []
    for item in drawing.get("items", []):
        kind = item[0]
        if kind == "re":
            rect = item[1]
            points.extend(
                [(rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1)]
            )
        elif kind == "qu":
            quad = item[1]
            points.extend((p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll))
        elif kind == "l":
            p1, p2 = item[1], item[2]
            if not points or points[-1] != (p1.x, p1.y):
                points.append((p1.x, p1.y))
            points.append((p2.x, p2.y))
    # drop an exact closing duplicate of the first vertex, if present
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    return points or None


def words_in_bbox(gt: SheetGroundTruth, x0: float, y0: float, x1: float, y1: float) -> str:
    """Concatenate ground-truth words whose center falls inside a tile's
    pixel bounding box, in reading order (top-to-bottom, left-to-right)."""
    hits = [w for w in gt.words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
    hits.sort(key=lambda w: (round(w.cy / 20), w.cx))
    return " ".join(w.text for w in hits)


def extract_table_rows(gt: SheetGroundTruth, x0: float, y0: float, x1: float, y1: float) -> list[list[str]]:
    """Group ground-truth words inside a bbox into visual table rows
    (by y-band, ordered left-to-right within each row), with zero model
    calls and no semantic interpretation.

    This is the "table structure first, meaning second" step agreed on for
    the specification-reading agent (see agents/spec_agent.py): a model
    reasoning over already row-grouped text is far more robust to spec-sheet
    layouts that vary between design offices (仕上表/建具表/仕様書一覧 etc.)
    than reasoning over an unordered word soup or the raw image alone.
    """
    hits = [w for w in gt.words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
    if not hits:
        return []

    ordered = sorted(hits, key=lambda w: w.cy)
    rows: list[list[GroundTruthWord]] = []
    for w in ordered:
        if rows and abs(w.cy - rows[-1][-1].cy) <= _TABLE_ROW_TOLERANCE_PT:
            rows[-1].append(w)
        else:
            rows.append([w])

    return [[w.text for w in sorted(row, key=lambda w: w.cx)] for row in rows]


def check_dimension_chains(gt: SheetGroundTruth) -> list[DimensionChainFlag]:
    """Cluster purely-numeric words into collinear bands per axis and cross
    check whether one band's total matches the sum of another band's
    members -- the arithmetic self-check a human estimator does by hand."""
    numeric = [w for w in gt.words if _NUMERIC_PATTERN.match(w.text.replace(",", ""))]
    if len(numeric) < _CHAIN_MIN_MEMBERS:
        return []

    # Note: clustering runs over *all* numeric words for both axes, not
    # split by GroundTruthWord.is_vertical first. A vertical dimension
    # chain's digits are not always drawn rotated (this CAD export mixes
    # both), so gating by glyph rotation before clustering silently dropped
    # real vertical chains typeset as upright text stacked along one x --
    # confirmed against the real sample drawing's left-edge dimension run.
    flags: list[DimensionChainFlag] = []
    flags.extend(
        _check_axis_bands(gt.sheet_id, numeric, key=lambda w: w.cy, perp_key=lambda w: w.cx, axis="horizontal")
    )
    flags.extend(
        _check_axis_bands(gt.sheet_id, numeric, key=lambda w: w.cx, perp_key=lambda w: w.cy, axis="vertical")
    )
    return flags


def find_dimension_chains(gt: SheetGroundTruth) -> list[dict]:
    """Public accessor for the collinear numeric chains ``check_dimension_chains``
    clusters internally, exposed for measurement.py's "project a span from the
    exterior dimension chain" reading (see measurement.py module docstring for
    the drawing-convention rationale: a real floor plan very often dimensions
    only its outer perimeter, and an interior wall's size is read by finding
    where it falls along that outer chain -- not from its own label, which
    frequently doesn't exist).

    Each entry: ``{"axis": "horizontal"|"vertical", "words": [GroundTruthWord, ...]}``,
    words ordered along the chain's primary axis (the axis the numbers are
    laid out along, i.e. ``perp_key`` in ``_band_cluster``'s terms).
    """
    numeric = [w for w in gt.words if _NUMERIC_PATTERN.match(w.text.replace(",", ""))]
    if len(numeric) < _CHAIN_MIN_MEMBERS:
        return []

    out = []
    for axis, key, perp_key in (
        ("horizontal", lambda w: w.cy, lambda w: w.cx),
        ("vertical", lambda w: w.cx, lambda w: w.cy),
    ):
        for band in _band_cluster(numeric, key, perp_key):
            out.append({"axis": axis, "words": sorted(band, key=perp_key)})
    return out


def _band_cluster(words: list[GroundTruthWord], key, perp_key) -> list[list[GroundTruthWord]]:
    """Group words into collinear runs.

    Two-stage: first group by the perpendicular coordinate (same text
    baseline row/column -- ``key``), then split each such group into
    sub-chains wherever the gap along the *primary* axis (``perp_key``) is
    much larger than the typical spacing between consecutive dimension
    labels. Without this second step, two unrelated dimension chains that
    merely happen to print at the same height (common: every top-edge
    dimension label on a sheet shares one baseline) get treated as one
    chain and produce meaningless sums -- confirmed against a real sample
    drawing, where a same-row chain otherwise wrongly absorbed an unrelated
    dimension from clear across the sheet.
    """
    ordered = sorted(words, key=key)
    rows: list[list[GroundTruthWord]] = []
    for w in ordered:
        if rows and abs(key(w) - key(rows[-1][-1])) <= _CHAIN_BAND_TOLERANCE_PT:
            rows[-1].append(w)
        else:
            rows.append([w])

    bands: list[list[GroundTruthWord]] = []
    for row in rows:
        row = sorted(row, key=perp_key)
        gaps = [perp_key(b) - perp_key(a) for a, b in zip(row, row[1:])]
        typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
        split_threshold = max(_CHAIN_MAX_GAP_PT, typical_gap * 3)

        current = [row[0]]
        for prev, w in zip(row, row[1:]):
            if perp_key(w) - perp_key(prev) > split_threshold:
                bands.append(current)
                current = [w]
            else:
                current.append(w)
        bands.append(current)

    return [b for b in bands if len(b) >= _CHAIN_MIN_MEMBERS]


def _check_axis_bands(sheet_id: str, words: list[GroundTruthWord], key, perp_key, axis: str) -> list[DimensionChainFlag]:
    if not words:
        return []
    bands = _band_cluster(words, key, perp_key)
    flags: list[DimensionChainFlag] = []

    for band in bands:
        values = [float(w.text.replace(",", "")) for w in band]
        band_sum = sum(values)
        band_ids = {id(w) for w in band}
        # A real chain-total is drawn spanning roughly the same range along
        # the *primary* axis as the components it totals (e.g. an overall
        # horizontal dimension sits above/below the same x-span as its
        # breakdown, just offset in y). Gating candidates on this catches
        # what plain value-matching alone does not: on a real sample
        # drawing, a coincidentally close number belonging to a completely
        # different, spatially distant dimension elsewhere on the same
        # sheet was otherwise wrongly matched as if it were this chain's
        # total.
        perp_values = [perp_key(w) for w in band]
        perp_lo, perp_hi = min(perp_values) - _CHAIN_TOTAL_SPAN_MARGIN_PT, max(perp_values) + _CHAIN_TOTAL_SPAN_MARGIN_PT

        for candidate in words:
            if id(candidate) in band_ids:
                continue
            if not (perp_lo <= perp_key(candidate) <= perp_hi):
                continue  # not spatially aligned with this chain -- unrelated number
            candidate_value = float(candidate.text.replace(",", ""))
            if candidate_value <= max(values):
                continue  # a "total" must be larger than any single component
            delta = abs(candidate_value - band_sum)
            if delta <= _CHAIN_MATCH_TOLERANCE_MM:
                continue  # matches within tolerance -- silently confirms, no flag
            # Only flag genuinely close misses (looks like it was meant
            # to be a chain-total but is off), not unrelated numbers --
            # otherwise this drowns real drawings in noise.
            if delta <= band_sum * 0.03:
                flags.append(
                    DimensionChainFlag(
                        sheet_id=sheet_id,
                        axis=axis,
                        component_raw_texts=[w.text for w in band],
                        component_sum_mm=band_sum,
                        total_raw_text=candidate.text,
                        total_value_mm=candidate_value,
                        delta_mm=candidate_value - band_sum,
                    )
                )
    return flags


def estimate_gross_footprint(gt: SheetGroundTruth) -> tuple[float, float, str] | None:
    """Estimate a sheet's overall (width_mm, depth_mm, basis) footprint from
    the largest horizontal and vertical dimension chains found on the sheet,
    returning ``None`` if no usable chain exists on either axis.

    This exists as a fallback for exactly the case that otherwise collapses
    to "不明": a sheet with no usable wall-fill polygons for
    ``wall_segments_mm`` (a scanned/rasterized drawing, a field-measured
    annotation overlay, or a CAD export whose wall-fill convention doesn't
    match ``_WALL_FILL_COLOR``) still very often has its overall dimension
    chain printed as text -- exactly what a human estimator reads off the
    outermost dimension line when a detailed area takeoff isn't otherwise
    available. The result is deliberately labeled as a *gross* (grid/outer
    envelope) figure, not a net interior area.

    Reuses the same per-axis collinear-chain clustering as
    ``check_dimension_chains`` rather than requiring its own vector layer
    -- the "largest chain sum on each axis" heuristic naturally picks out
    the overall span over smaller room-by-room breakdown chains, since the
    whole is definitionally >= any of its parts. Deliberately not gated to
    any particular sheet type: this is run against every sheet with vector
    ground truth (see orchestrator.py), on the same "don't assume only the
    obviously-relevant document has the answer" principle used to fold
    COAI-01 spec facts into Phase 1 -- an electrical or MEP sheet can carry
    the same outer dimension chain as the floor plan it was traced from.
    """
    numeric = [w for w in gt.words if _NUMERIC_PATTERN.match(w.text.replace(",", ""))]
    if len(numeric) < _CHAIN_MIN_MEMBERS:
        return None

    h_bands = _band_cluster(numeric, key=lambda w: w.cy, perp_key=lambda w: w.cx)
    v_bands = _band_cluster(numeric, key=lambda w: w.cx, perp_key=lambda w: w.cy)

    def _largest_span_mm(bands: list[list[GroundTruthWord]]) -> float | None:
        sums = [sum(float(w.text.replace(",", "")) for w in band) for band in bands]
        return max(sums) if sums else None

    width_mm = _largest_span_mm(h_bands)
    depth_mm = _largest_span_mm(v_bands)
    if width_mm is None or depth_mm is None:
        return None

    basis = f"最大寸法チェーン(横={width_mm:.0f}mm, 縦={depth_mm:.0f}mm)による外形概算"
    return width_mm, depth_mm, basis


def wall_segments_mm(gt: SheetGroundTruth) -> list[WallSegmentGeometry]:
    if gt.mm_per_px is None:
        return []
    out = []
    for poly_px in gt.wall_polygons_px:
        poly_mm = [[x * gt.mm_per_px, y * gt.mm_per_px] for x, y in poly_px]
        out.append(WallSegmentGeometry(sheet_id=gt.sheet_id, polygon_mm=poly_mm))
    return out
