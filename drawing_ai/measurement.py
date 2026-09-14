"""Length/area measurement reasoning, deterministic and zero-model-call --
the digital equivalent of the two techniques a human estimator actually
uses when reading a real architectural drawing (confirmed against real
project drawings, not assumed):

1. **Projecting off the exterior dimension chain.** A real floor plan very
   often does NOT dimension every interior wall individually. Instead,
   dimensions run along the drawing's outer edges as a chain of numbers
   between grid lines (通り芯), and a human finds an undimensioned interior
   wall's size by tracing (projecting) horizontally/vertically from that
   wall's position to the matching segment of the exterior chain. This is
   the *normal* way to read a drawing, not a fallback -- so a pipeline that
   only sums dimension chains for an overall footprint (as
   ``vector_extractor.estimate_gross_footprint`` does) and never projects a
   chain onto a specific interior element's position is missing the
   technique that actually resolves most real room/wall dimensions.
   ``project_span_mm`` implements this: given a dimension chain (from
   ``vector_extractor.find_dimension_chains``) and a target pixel range
   (typically a wall or room's own bounding extent along that axis), it
   sums the chain members whose printed position falls inside that range --
   the same left-to-right/top-to-bottom tracing a human does by eye.

2. **The 三角スケール (architect's scale ruler).** When a drawing has NO
   printed dimension at all for the thing being measured, but its overall
   print scale IS known (e.g. "1/50"), a human lays a scale ruler on the
   page and reads the real-world length off the printed one. The digital
   equivalent needs only two pixel points and the sheet's ``mm_per_px``
   (already computed by ``vector_extractor.extract_ground_truth`` when a
   scale token is found) -- ``pixel_distance_to_mm`` and ``polygon_area_sqm``
   do this for a length and an enclosed area respectively.

Both techniques require a known ``mm_per_px`` (technique 2) or a legible
dimension chain (technique 1) to be *present on the sheet in the first
place* -- neither invents a measurement a real estimator couldn't also make
from the same drawing.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .vector_extractor import GroundTruthWord

_NUMERIC_VALUE = re.compile(r"^[0-9][0-9,]*$")


def pixel_distance_to_mm(p1_px: tuple[float, float], p2_px: tuple[float, float], mm_per_px: float) -> float:
    """The 三角スケール technique: a raw pixel distance, converted to a real
    length using the sheet's print scale -- no dimension text involved."""
    dx = p2_px[0] - p1_px[0]
    dy = p2_px[1] - p1_px[1]
    return math.hypot(dx, dy) * mm_per_px


def polygon_area_sqm(polygon_px: list[tuple[float, float]], mm_per_px: float) -> float:
    """Shoelace-formula area of any closed pixel polygon (a room outline, a
    wall footprint, an opening) converted to real square meters via the
    sheet's print scale -- the area equivalent of ``pixel_distance_to_mm``."""
    if len(polygon_px) < 3:
        return 0.0
    area_px2 = 0.0
    n = len(polygon_px)
    for i in range(n):
        x1, y1 = polygon_px[i]
        x2, y2 = polygon_px[(i + 1) % n]
        area_px2 += x1 * y2 - x2 * y1
    area_px2 = abs(area_px2) / 2.0
    area_mm2 = area_px2 * (mm_per_px**2)
    return area_mm2 / 1_000_000.0


@dataclass
class ChainProjectionResult:
    total_mm: float
    matched_texts: list[str]
    coverage_note: str


def project_span_mm(
    chain_words: list[GroundTruthWord],
    lo_px: float,
    hi_px: float,
    *,
    perp_px: float | None = None,
    max_perp_distance_px: float = 400.0,
    tolerance_px: float = 5.0,
) -> ChainProjectionResult | None:
    """Project a target pixel range (e.g. a wall's own bounding extent along
    the chain's axis) onto a dimension chain's members, matching by each
    member's OWN label position against the target range.

    Honest scope, confirmed against a real drawing (see
    tests/test_measurement.py and the amusement-facility page-4 wall/column
    check this was built against): this only finds a match when a chain
    member's *own printed label* happens to sit within the target range. It
    is NOT full grid reconstruction -- a first attempt at that (assuming
    each label sits at its segment's exact midpoint, then least-squares
    fitting the chain's pixel origin from the known mm_per_px) was tried
    against real page-4 column positions and failed badly: predicted grid
    lines were 400-900mm off from the columns' actual real positions. Real
    CAD dimension conventions push a short segment's label off-center via a
    leader line, breaking the midpoint assumption, and reconstructing this
    properly needs the actual extension/witness-line vector geometry (thin
    dimension lines + tick marks), not text positions alone -- not
    implemented here. So this function is deliberately conservative: it
    only claims a match where a label is directly in range, and returns
    ``None`` rather than a guessed value everywhere else.

    ``chain_words`` must already be ordered along the chain's own axis (as
    returned by ``vector_extractor.find_dimension_chains``); this function is
    axis-agnostic -- the caller picks ``perp_key`` consistent with the
    chain's ``axis`` (cx for a vertical chain's members, cy for a
    horizontal one) when computing ``lo_px``/``hi_px`` and is expected to use
    the matching coordinate of each word too. In practice callers pass
    already-axis-selected positions; see review_ui/dimension_review.py for
    the concrete wiring.

    ``perp_px`` (recommended, see the false-positive note below): the
    target's own position along the axis PERPENDICULAR to the chain (e.g.
    a horizontal chain's members share roughly one cy -- pass the target
    wall's cy here). When given, a chain whose own mean perpendicular
    position is more than ``max_perp_distance_px`` away is rejected
    outright, before any label-range matching. This gating is not
    optional in practice: without it, this function was found (against
    real page-4 data, see README 3.11) to report "matches" purely from x
    (or y) coincidentally overlapping a completely unrelated dimension
    chain sitting hundreds of millimeters away in the other axis --
    3 of 4 test columns first appeared to "match" a chain this way, and
    every one of those matches was spurious once the perpendicular
    distance was checked (the nearest real chain sat ~1000px / ~6m away
    in cy). Callers that omit ``perp_px`` get the old, ungated behavior --
    keep it that way for review_ui/dimension_review.py's own multi-chain
    search loop, which already iterates candidate chains and should pass
    ``perp_px`` itself; a caller with only one known-relevant chain in
    hand may reasonably skip it.

    Returns ``None`` if no chain member's position falls anywhere in range,
    or (when ``perp_px`` is given) if no chain is close enough on the
    perpendicular axis to plausibly be the one this target belongs to --
    the caller should fall back to some other technique, e.g.
    ``pixel_distance_to_mm`` against a known scale, or flag the span as
    needing a human to trace it visually.
    """

    # Match on each word's own pixel center along whichever axis has the
    # larger spread across the whole chain -- avoids needing the caller to
    # pass the axis separately while staying correct for both orientations.
    if not chain_words:
        return None
    xs = [w.cx for w in chain_words]
    ys = [w.cy for w in chain_words]
    axis_is_x = (max(xs) - min(xs)) >= (max(ys) - min(ys))
    positions = xs if axis_is_x else ys

    if perp_px is not None:
        perp_positions = ys if axis_is_x else xs
        chain_perp = sum(perp_positions) / len(perp_positions)
        if abs(chain_perp - perp_px) > max_perp_distance_px:
            return None

    matched = []
    for w, pos in zip(chain_words, positions):
        if lo_px - tolerance_px <= pos <= hi_px + tolerance_px:
            matched.append(w)

    if not matched:
        return None

    total = sum(float(w.text.replace(",", "")) for w in matched if _NUMERIC_VALUE.match(w.text.replace(",", "")))
    span_lo = min(positions[chain_words.index(w)] for w in matched)
    span_hi = max(positions[chain_words.index(w)] for w in matched)
    coverage = "target range fully covered by matched members" if (span_lo <= lo_px + tolerance_px and span_hi >= hi_px - tolerance_px) else "partial coverage -- target range extends beyond matched chain members"

    return ChainProjectionResult(
        total_mm=total,
        matched_texts=[w.text for w in matched],
        coverage_note=coverage,
    )
