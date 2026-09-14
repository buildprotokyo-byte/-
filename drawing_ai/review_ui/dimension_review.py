"""Measurement-verification review data: area/length/footprint figures are
*derived* (summed, multiplied, matched against a printed total) rather than
read directly off the page, which makes them a different -- and higher-risk
-- kind of error than a single misread character. This module surfaces that
derivation so a human can check the arithmetic, not just accept a final
number.

Concretely this wraps two already-implemented, zero-model-call checks in
vector_extractor.py:

- ``check_dimension_chains``: re-adds each collinear run of dimension
  numbers and flags when it disagrees with a nearby printed "total" label
  -- the same sanity check a human estimator does by hand with a
  calculator. Confirmed against two real projects (see
  ``build_dimension_review_data`` usage in the review tooling): the
  amusement-facility drawing's page 4 has three such real, non-trivial
  mismatches (e.g. a horizontal run summing to 17450mm against a printed
  "17225" total, 225mm off) -- this is not a synthetic example.
- ``estimate_gross_footprint``: the "largest chain per axis = overall
  footprint" fallback used when no wall-fill polygon is available. This is
  a heuristic, and notably is NOT currently cross-checked against
  ``check_dimension_chains``' own findings -- if the sheet's largest chain
  is itself flagged as disagreeing with a printed total, the footprint
  estimate silently doesn't know that. Surfacing both together here is the
  first step toward fixing that gap; the actual cross-check (downgrading
  ``estimate_gross_footprint``'s confidence when its source chain carries a
  flag) is not yet wired into vector_extractor.py -- see README 3.10.
"""
from __future__ import annotations

from .. import vector_extractor as ve


def build_dimension_review_data(gt: ve.SheetGroundTruth, sheet_label: str = "sheet") -> dict:
    flags = ve.check_dimension_chains(gt)
    footprint = ve.estimate_gross_footprint(gt)

    flags_out = [
        {
            "axis": f.axis,
            "component_raw_texts": f.component_raw_texts,
            "component_sum_mm": f.component_sum_mm,
            "total_raw_text": f.total_raw_text,
            "total_value_mm": f.total_value_mm,
            "delta_mm": f.delta_mm,
        }
        for f in flags
    ]

    footprint_out = None
    if footprint is not None:
        width_mm, depth_mm, basis = footprint
        # honest cross-check: does *this* sheet carry any chain mismatch at
        # all? if so, the footprint heuristic (itself chain-based) deserves
        # a lower-confidence label even though nothing here proves the
        # specific chain it used is the flagged one.
        footprint_out = {
            "width_mm": width_mm,
            "depth_mm": depth_mm,
            "basis": basis,
            "sheet_has_unrelated_chain_mismatches": len(flags_out) > 0,
        }

    return {
        "sheet_label": sheet_label,
        "scale_text": gt.scale_text,
        "mm_per_px": gt.mm_per_px,
        "dimension_chain_flags": flags_out,
        "gross_footprint": footprint_out,
    }
