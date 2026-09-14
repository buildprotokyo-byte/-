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

from .. import measurement as ms
from .. import vector_extractor as ve


def build_dimension_review_data(
    gt: ve.SheetGroundTruth,
    sheet_label: str = "sheet",
    *,
    projection_targets: list[dict] | None = None,
) -> dict:
    """``projection_targets`` (optional): elements to test
    ``measurement.project_span_mm`` against, e.g. wall/column bounding
    boxes a human might expect to read off the exterior chain -- each
    ``{"label": str, "axis": "horizontal"|"vertical", "lo_px": float, "hi_px": float, "perp_px": float}``.
    ``perp_px`` is the target's own position on the axis perpendicular to
    ``axis`` (its cy for a horizontal target, cx for a vertical one) --
    required to reject a chain that merely happens to overlap in one axis
    while sitting far away in the other (see measurement.project_span_mm's
    docstring: this was a real false-positive found against page-4 data,
    not a hypothetical).

    Included so the review page can show, honestly, which targets the
    projection technique actually resolves and which it doesn't (see
    measurement.project_span_mm's docstring for why this is often "doesn't
    resolve" on a real drawing) -- this is not a demo of a feature that
    always works.
    """
    flags = ve.check_dimension_chains(gt)
    footprint = ve.estimate_gross_footprint(gt)
    chains = ve.find_dimension_chains(gt)

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

    chains_out = [
        {
            "axis": c["axis"],
            "words": [{"text": w.text, "cx": w.cx, "cy": w.cy, "x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1} for w in c["words"]],
            "sum_mm": sum(float(w.text.replace(",", "")) for w in c["words"]),
        }
        for c in chains
    ]

    projections_out = []
    for target in projection_targets or []:
        best = None
        for c in chains:
            if c["axis"] != target["axis"]:
                continue
            result = ms.project_span_mm(
                c["words"], target["lo_px"], target["hi_px"], perp_px=target.get("perp_px")
            )
            if result is not None:
                best = result
                break
        projections_out.append(
            {
                "label": target["label"],
                "axis": target["axis"],
                "lo_px": target["lo_px"],
                "hi_px": target["hi_px"],
                "matched": best is not None,
                "total_mm": best.total_mm if best else None,
                "matched_texts": best.matched_texts if best else [],
                "coverage_note": best.coverage_note if best else "この範囲に収まる寸法チェーンのラベルが見つからなかった(投影不可)",
            }
        )

    return {
        "sheet_label": sheet_label,
        "scale_text": gt.scale_text,
        "mm_per_px": gt.mm_per_px,
        "dimension_chains": chains_out,
        "dimension_chain_flags": flags_out,
        "gross_footprint": footprint_out,
        "projection_tests": projections_out,
    }
