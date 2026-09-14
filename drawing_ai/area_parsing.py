"""Parses a free-text room-area label into square meters.

Japanese floor plans commonly print a room's area as "5.3m2", "12.5畳"
(tatami-mat count), or both together ("5.3m2|3.2畳", as seen on the Yano
drawing set). This is a small, standalone deterministic parser -- not an
AI call -- used by detail_agent.py to turn whatever area text a child
agent transcribed into a numeric ``ElementReading.area_sqm`` value, rather
than leaving it stranded as unstructured text in ``attributes``.
"""
from __future__ import annotations

import re

_SQM_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:m2|m\^2|㎡|平米)")
_TATAMI_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*畳")

# The common real-estate-listing conversion factor (1 畳 ≈ 1.62 m2). Actual
# tatami dimensions vary by region/convention (京間・江戸間・団地間 etc.), so
# this is a documented approximation, not an exact figure -- callers should
# prefer an explicit m2 label over this conversion when both are present.
TATAMI_TO_SQM = 1.62


def parse_area_text(text: str) -> tuple[float, str] | None:
    """Return (area_sqm, source) for the first recognizable area figure in
    ``text``, or ``None`` if nothing matches.

    An explicit m2/㎡ figure is preferred over a tatami-count conversion
    when a label carries both (e.g. "5.3m2|3.2畳") -- the mat count on such
    labels is itself usually just the m2 figure rounded to the nearest
    half-mat for display, so it is the less precise of the two.
    """
    if not text:
        return None
    m = _SQM_PATTERN.search(text)
    if m:
        return float(m.group(1)), "printed_sqm"
    m = _TATAMI_PATTERN.search(text)
    if m:
        return round(float(m.group(1)) * TATAMI_TO_SQM, 2), "tatami_conversion"
    return None
