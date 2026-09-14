"""Resolves internal cross-references inside Q&A-style spec documents.

A real project's specification material is often an RFI-style Q&A table
(質疑書: No. / 工種 / 質疑事項 / 回答), and one answer commonly defers to
another row instead of restating it ("質疑No.6参照" -- "see item 6"). A
blind test against a real project (KDX802) produced a concrete wrong
answer from exactly this: a vanity-top question whose answer deferred to
a separate removal decision was read as "reuse" because the reference was
never followed to what item 6 actually said. Treating each row as
independent text silently drops the real answer.
"""
from __future__ import annotations

import re

_REFERENCE_PATTERN = re.compile(r"(?:質疑)?No\.?\s*(\d+)\s*参照")


def find_reference(text: str) -> int | None:
    """Return the referenced item number if ``text`` defers to another
    item (e.g. "質疑No.6参照"), else ``None``."""
    m = _REFERENCE_PATTERN.search(text or "")
    return int(m.group(1)) if m else None


def resolve_references(
    items: list[dict], *, number_key: str = "item_no", answer_key: str = "answer"
) -> list[dict]:
    """Follow one hop of "質疑No.X参照"-style cross-references in a list
    of Q&A rows, appending the referenced row's own answer text so a
    downstream reader never has to guess what "参照" points to.

    Only follows a single hop -- a reference that itself only points to
    yet another reference is left unresolved and flagged via
    ``unresolved_chained_reference`` rather than chased further, since
    each additional hop increases the risk of silently resolving to the
    wrong place.
    """
    by_number = {item.get(number_key): item for item in items}
    resolved: list[dict] = []
    for item in items:
        item = dict(item)
        ref = find_reference(str(item.get(answer_key, "")))
        if ref is not None and ref in by_number and ref != item.get(number_key):
            target = by_number[ref]
            target_answer = str(target.get(answer_key, ""))
            if find_reference(target_answer) is None:
                item[answer_key] = (
                    f"{item.get(answer_key, '')} → (質疑No.{ref}の回答: {target_answer})"
                )
                item["resolved_from_reference"] = ref
            else:
                item["unresolved_chained_reference"] = ref
        resolved.append(item)
    return resolved
