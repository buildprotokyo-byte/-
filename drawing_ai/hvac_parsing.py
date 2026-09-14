"""Parses Japanese multi-split air-conditioning system notation.

A spec document commonly describes a multi-split AC system's configuration
in prose, e.g. "1対1×2組、1対2×1組" ("two sets of 1-outdoor-to-1-indoor,
plus one set of 1-outdoor-to-2-indoor"). A blind test against a real
project read this by eye and guessed 3 outdoor / 4 indoor units; the
equipment actually specified elsewhere in the same project (model numbers
"3M685AV" + "2M535AV" -- Daikin's multi-split naming, where the leading
digit is the branch/indoor-unit count) was 2 outdoor units. The two
sources disagreed -- the prose described a system state that didn't match
the final specified equipment -- and guessing from the prose alone picked
the wrong one with no visibility into the conflict.

This module replaces the eyeballing with two deterministic parsers (the
prose pattern, and the equipment model-code pattern) plus a comparison
that surfaces disagreement instead of silently trusting either source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SYSTEM_PATTERN = re.compile(r"(\d+)\s*対\s*(\d+)\s*[×xX]\s*(\d+)\s*組")
# Daikin-style multi-split outdoor unit model codes: a leading digit
# followed by "M" gives the branch count (how many indoor units this one
# outdoor unit can serve), e.g. "3M685AV" -> 3-branch, "2M535AV" -> 2-branch.
_DAIKIN_MODEL_PATTERN = re.compile(r"^(\d)M\d")


@dataclass
class MultiSplitSystem:
    outdoor_per_set: int
    indoor_per_outdoor: int
    set_count: int

    @property
    def outdoor_units(self) -> int:
        return self.outdoor_per_set * self.set_count

    @property
    def indoor_units(self) -> int:
        return self.indoor_per_outdoor * self.set_count


def parse_multi_split_notation(text: str) -> list[MultiSplitSystem]:
    """Parse every "N対M×K組" occurrence in ``text``. Returns an empty
    list if the pattern isn't found -- callers should not guess a count
    from surrounding prose when this comes back empty."""
    systems = []
    for m in _SYSTEM_PATTERN.finditer(text or ""):
        outdoor_per_set, indoor_per_outdoor, set_count = (int(g) for g in m.groups())
        systems.append(MultiSplitSystem(outdoor_per_set, indoor_per_outdoor, set_count))
    return systems


def total_units(systems: list[MultiSplitSystem]) -> tuple[int, int]:
    """Return (total_outdoor_units, total_indoor_units) implied by a
    parsed prose description."""
    return sum(s.outdoor_units for s in systems), sum(s.indoor_units for s in systems)


def parse_daikin_branch_count(model_number: str) -> int | None:
    """Return the indoor-unit branch count encoded in a Daikin multi-split
    outdoor unit model number (e.g. "3M685AV" -> 3), or ``None`` if the
    model number doesn't match this convention."""
    m = _DAIKIN_MODEL_PATTERN.match((model_number or "").strip())
    return int(m.group(1)) if m else None


def check_prose_vs_equipment(
    prose_text: str, outdoor_model_numbers: list[str]
) -> tuple[int, int, list[str]]:
    """Reconcile a prose system description against actual equipment model
    numbers, preferring the model numbers (a concrete equipment spec) when
    both are available and they disagree -- the equipment schedule is
    closer to a ground-truth source than a system-state description in an
    RFI answer, which can describe an existing setup being changed rather
    than the final specified one.

    Returns (outdoor_units, max_indoor_capacity, conflict_notes).
    ``max_indoor_capacity`` is the branch-count ceiling the outdoor units
    support, not a confirmed indoor-unit count -- not every branch a
    multi-split outdoor unit supports is necessarily connected (some may
    serve existing indoor units left untouched by this scope), so this is
    always returned with a caveat note rather than presented as exact.
    """
    notes: list[str] = []
    prose_systems = parse_multi_split_notation(prose_text)
    prose_outdoor, prose_indoor = total_units(prose_systems)

    branch_counts = [c for c in (parse_daikin_branch_count(m) for m in outdoor_model_numbers) if c is not None]
    equipment_outdoor = len(outdoor_model_numbers)
    equipment_indoor_capacity = sum(branch_counts) if branch_counts else 0

    if not outdoor_model_numbers:
        return prose_outdoor, prose_indoor, notes

    if branch_counts:
        notes.append(
            f"室内機台数は室外機型番から算出した「対応可能な最大分岐数」({equipment_indoor_capacity}台)"
            f"であり、全分岐が実際に接続されているとは限りません(既存機を残す分岐がある可能性)。要確認。"
        )

    if prose_systems and (prose_outdoor != equipment_outdoor or prose_indoor != equipment_indoor_capacity):
        notes.append(
            f"系統の記述(「{prose_text}」→室外機{prose_outdoor}台/室内機{prose_indoor}台)と、"
            f"実際の機器型番(室外機{equipment_outdoor}台/最大分岐{equipment_indoor_capacity}台)が食い違います。"
            f"機器型番を優先しましたが、記述は工事前の既存状態を指している可能性があり要確認。"
        )

    return equipment_outdoor, equipment_indoor_capacity, notes
