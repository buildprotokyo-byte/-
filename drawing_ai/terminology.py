"""Canonical finish/material terminology.

A designer's free-text finish label varies by design office (「CL」「クロス」
「ビニールクロス」all mean the same thing). spec_agent.py normalizes these to
one canonical term where a mapping is known, while always preserving the raw
text alongside it (see schemas.SpecItem.raw_finish_term), so searching or
aggregating specs across projects from different offices is possible without
losing the literal wording or silently guessing at an unknown term.

The glossary below is a starting point derived from common terminology used
in Japanese public-works finish schedules (国土交通省 公共建築工事標準仕様書
等に準じた略号・呼称), not an exhaustive catalog -- new variants encountered
in real specs should be added here rather than handled ad hoc inside the
agent prompt.
"""
from __future__ import annotations

_CANONICAL_FINISH_TERMS: dict[str, str] = {
    "cl": "ビニールクロス張り",
    "クロス": "ビニールクロス張り",
    "クロス張り": "ビニールクロス張り",
    "ビニルクロス": "ビニールクロス張り",
    "ビニールクロス": "ビニールクロス張り",
    "fl": "フローリング張り",
    "フローリング": "フローリング張り",
    "床フローリング": "フローリング張り",
    "複合フローリング": "フローリング張り",
    "無垢フローリング": "フローリング張り(無垢)",
    "cf": "クッションフロア張り",
    "クッションフロア": "クッションフロア張り",
    "cft": "タイルカーペット張り",
    "タイルカーペット": "タイルカーペット張り",
    "ep": "EP塗装(合成樹脂エマルションペイント塗り)",
    "エマルションペイント": "EP塗装(合成樹脂エマルションペイント塗り)",
    "sop": "SOP塗装(合成樹脂調合ペイント塗り)",
    "vp": "VP塗装(ビニル系エナメルペイント塗り)",
    "aep": "AEP塗装(つや有り合成樹脂エマルションペイント塗り)",
    "pb": "石膏ボード張り",
    "石膏ボード": "石膏ボード張り",
    "プラスターボード": "石膏ボード張り",
    "ケイカル板": "ケイ酸カルシウム板張り",
    "珪酸カルシウム板": "ケイ酸カルシウム板張り",
    "タイル": "タイル張り",
    "モルタル": "モルタル塗り",
    "左官": "左官仕上げ",
}


def normalize_term(raw: str) -> str | None:
    """Return the canonical term for a raw finish description, or ``None``
    if no known mapping applies.

    Always returns ``None`` rather than guessing -- an unmapped term is
    preserved as-is by the caller (see spec_agent._make_spec_item), never
    forced into the wrong bucket.
    """
    key = raw.strip().replace(" ", "").replace("　", "").lower()
    if not key:
        return None
    return _CANONICAL_FINISH_TERMS.get(key)
