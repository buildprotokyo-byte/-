"""The parent agent: merges every child agent's output into one result.

Two layers of merging happen:
1. Deterministic dedup across tiles -- tile overlap (see ``ingestion.py``)
   means the same dimension/symbol/element can legitimately appear in two
   neighboring tiles' outputs; these are collapsed here by simple key
   matching before anything is sent to a model, since an LLM should not be
   needed (or trusted) to do exact-duplicate removal.
2. LLM-based reconciliation -- for the harder case of genuinely conflicting
   readings of the *same* real-world dimension/symbol (two child agents
   disagree on a value), the parent LLM (the larger model in config.py) is
   given both readings plus their evidence and asked to pick the more
   plausible one and record the disagreement in ``low_confidence_flags``.
   This step is skipped (falling back to the higher-confidence deterministic
   pick) if the parent LLM call fails, so the pipeline degrades gracefully
   instead of losing all Phase 3 output.
"""
from __future__ import annotations

import json
import logging

from ..config import settings
from ..prompts import PARENT_AGGREGATE_SYSTEM_PROMPT, PARENT_AGGREGATE_USER_TEMPLATE
from ..schemas import (
    DimensionReading,
    ElementReading,
    IntentStatement,
    Phase1Result,
    Phase2Result,
    Phase3Result,
    SiteFact,
    SymbolReading,
    VerticalSynthesisNote,
)
from ..vlm_client import VLMCallError, extract_json, get_client

logger = logging.getLogger("drawing_ai.agents.parent")

# Two dimension readings are treated as "the same real dimension" if their
# raw text matches after normalization AND they come from tiles on the same
# sheet -- deliberately conservative to avoid merging two different "2,730"
# measurements that happen to appear on different sheets.
def _dim_key(d: DimensionReading, tile_sheet: dict[str, str]) -> tuple[str, str]:
    return (tile_sheet.get(d.tile_id, ""), d.raw_text.strip())


def _dedupe_dimensions(items: list[DimensionReading], tile_sheet: dict[str, str]) -> list[DimensionReading]:
    best: dict[tuple[str, str], DimensionReading] = {}
    for d in items:
        key = _dim_key(d, tile_sheet)
        current = best.get(key)
        if current is None or d.confidence > current.confidence:
            best[key] = d
    return list(best.values())


def _dedupe_symbols(items: list[SymbolReading], tile_sheet: dict[str, str]) -> list[SymbolReading]:
    best: dict[tuple[str, str], SymbolReading] = {}
    for s in items:
        key = (tile_sheet.get(s.tile_id, ""), s.symbol_text_or_glyph.strip())
        current = best.get(key)
        if current is None or s.confidence > current.confidence:
            best[key] = s
    return list(best.values())


def _dedupe_elements(items: list[ElementReading]) -> list[ElementReading]:
    best: dict[tuple[str, str, str], ElementReading] = {}
    for e in items:
        key = (e.sheet_id, e.element_type, e.label_ja.strip())
        current = best.get(key)
        if current is None:
            best[key] = e
        elif e.confidence > current.confidence:
            merged = e.model_copy()
            merged.tile_ids = list(set(current.tile_ids) | set(e.tile_ids))
            best[key] = merged
        else:
            current.tile_ids = list(set(current.tile_ids) | set(e.tile_ids))
    return list(best.values())


LOW_CONFIDENCE_THRESHOLD = 0.5


async def aggregate_phase3(
    all_dimensions: list[DimensionReading],
    all_symbols: list[SymbolReading],
    all_elements: list[ElementReading],
    vertical_notes: list[VerticalSynthesisNote],
    tile_sheet: dict[str, str],
    *,
    use_llm_reconciliation: bool = True,
) -> Phase3Result:
    dimensions = _dedupe_dimensions(all_dimensions, tile_sheet)
    symbols = _dedupe_symbols(all_symbols, tile_sheet)
    elements = _dedupe_elements(all_elements)

    low_confidence_flags = [
        f"寸法「{d.raw_text}」の確信度が低いため要確認 (tile={d.tile_id}, confidence={d.confidence:.2f})"
        for d in dimensions
        if d.confidence < LOW_CONFIDENCE_THRESHOLD
    ] + [
        f"記号「{s.symbol_text_or_glyph}」の確信度が低いため要確認 (tile={s.tile_id}, confidence={s.confidence:.2f})"
        for s in symbols
        if s.confidence < LOW_CONFIDENCE_THRESHOLD
    ]

    if use_llm_reconciliation and (dimensions or symbols or elements):
        dimensions, symbols, elements, extra_flags = await _llm_reconcile(dimensions, symbols, elements)
        low_confidence_flags.extend(extra_flags)

    all_confidences = [d.confidence for d in dimensions] + [s.confidence for s in symbols] + [
        e.confidence for e in elements
    ]
    accuracy_estimate = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

    return Phase3Result(
        dimensions=dimensions,
        symbols=symbols,
        elements=elements,
        vertical_synthesis=vertical_notes,
        low_confidence_flags=low_confidence_flags,
        accuracy_estimate=accuracy_estimate,
    )


async def _llm_reconcile(
    dimensions: list[DimensionReading],
    symbols: list[SymbolReading],
    elements: list[ElementReading],
) -> tuple[list[DimensionReading], list[SymbolReading], list[ElementReading], list[str]]:
    """Ask the parent LLM to sanity-check the deduped set.

    Only sent when there is a manageable amount of material -- for a very
    large drawing set this is capped so the parent call itself doesn't
    become the bottleneck against the 15-30 minute time budget; anything
    beyond the cap is passed through untouched from the deterministic dedup.
    """
    cap = 250
    if len(dimensions) + len(symbols) + len(elements) > cap:
        logger.info("skipping LLM reconciliation: %d items exceeds cap", len(dimensions) + len(symbols) + len(elements))
        return dimensions, symbols, elements, []

    client = get_client(settings.parent_llm)
    payload = {
        "dimensions": [d.model_dump() for d in dimensions],
        "symbols": [s.model_dump() for s in symbols],
        "elements": [e.model_dump() for e in elements],
    }
    prompt = PARENT_AGGREGATE_USER_TEMPLATE.format(
        raw_child_outputs_json=json.dumps(payload, ensure_ascii=False)
    )

    try:
        response = await client.chat(
            PARENT_AGGREGATE_SYSTEM_PROMPT,
            prompt,
            image_paths=None,
            max_tokens=4000,
        )
        data = extract_json(response.text)
        new_dims = [DimensionReading(**_with_defaults(d, dimensions)) for d in data.get("dimensions", [])]
        new_syms = [SymbolReading(**_with_defaults(s, symbols)) for s in data.get("symbols", [])]
        new_elems = [ElementReading(**_with_defaults(e, elements)) for e in data.get("elements", [])]
        flags = [str(f) for f in data.get("low_confidence_flags", [])]
        return new_dims, new_syms, new_elems, flags
    except (VLMCallError, ValueError, KeyError, TypeError) as exc:
        logger.warning("parent LLM reconciliation failed, keeping deterministic merge: %s", exc)
        return dimensions, symbols, elements, [f"親AIによる統合検証に失敗したため機械的な統合結果を採用: {exc}"]


def _with_defaults(item: dict, originals: list) -> dict:
    """Fill in required fields the reconciliation LLM might drop (e.g. tile_id)."""
    item = dict(item)
    if not item.get("tile_id") and originals:
        item["tile_id"] = getattr(originals[0], "tile_id", "unknown")
    if not item.get("sheet_id") and originals and hasattr(originals[0], "sheet_id"):
        item["sheet_id"] = getattr(originals[0], "sheet_id", "unknown")
    if "element_id" in _fields_of(originals) and not item.get("element_id"):
        item["element_id"] = f"reconciled-{abs(hash(json.dumps(item, sort_keys=True, ensure_ascii=False))) % 10**8}"
    return item


def _fields_of(originals: list) -> set[str]:
    if not originals:
        return set()
    return set(type(originals[0]).model_fields.keys())


# The set of facts a complete 敷地情報 read should have. Used only to compute
# Phase1Result.completeness_score / unresolved_questions -- it never
# discards a fact, it just tells the caller (and eventually a human
# reviewer) what is still missing before Phase 1's 100% accuracy bar can be
# considered met.
EXPECTED_SITE_KEYS = {
    "site_area_sqm": "敷地面積",
    "frontage_road_width_m": "前面道路幅員",
    "zoning": "用途地域",
    "building_coverage_ratio": "建蔽率",
    "floor_area_ratio": "容積率",
    "setback_m": "セットバック",
    "address": "所在地",
}


def aggregate_phase1(all_facts: list[SiteFact]) -> Phase1Result:
    """Dedupe site facts by key, preferring the highest-confidence reading."""
    best: dict[str, SiteFact] = {}
    for fact in all_facts:
        current = best.get(fact.key)
        if current is None or fact.confidence > current.confidence:
            best[fact.key] = fact
        elif fact.confidence == current.confidence:
            # Independent agreement across tiles/sheets on the same fact is
            # itself evidence -- merge sources and nudge confidence up.
            current.source_tile_ids = list(set(current.source_tile_ids) | set(fact.source_tile_ids))
            current.source_sheet_ids = list(set(current.source_sheet_ids) | set(fact.source_sheet_ids))
            current.confidence = min(1.0, current.confidence + 0.05)
            current.needs_human_review = current.confidence < 0.8

    facts = list(best.values())
    found_keys = {f.key for f in facts}
    missing = [label for key, label in EXPECTED_SITE_KEYS.items() if key not in found_keys]
    completeness = 1.0 - (len(missing) / len(EXPECTED_SITE_KEYS)) if EXPECTED_SITE_KEYS else 1.0

    unresolved = [f"「{label}」が図面から読み取れませんでした。手動確認が必要です。" for label in missing]
    unresolved += [
        f"「{f.label_ja or f.key}」は確信度が低いため({f.confidence:.2f})要確認: {f.raw_evidence_text}"
        for f in facts
        if f.needs_human_review
    ]

    return Phase1Result(facts=facts, completeness_score=completeness, unresolved_questions=unresolved)


def aggregate_phase2(statements: list[IntentStatement], project_summary_ja: str = "") -> Phase2Result:
    categories: list[str] = []
    for s in statements:
        for c in s.construction_categories:
            if c not in categories:
                categories.append(c)

    return Phase2Result(
        project_summary_ja=project_summary_ja,
        statements=statements,
        inferred_construction_scope=categories,
    )


async def summarize_project(statements: list[IntentStatement]) -> str:
    """One parent-LLM call that folds all per-tile intent statements into a
    single project-level introduction paragraph, suitable as the opening of
    an estimate document."""
    if not statements:
        return ""

    client = get_client(settings.parent_llm)
    joined = "\n".join(f"- {s.text_ja}" for s in statements[:200])
    prompt = (
        "以下は同一の図面一式から、部分ごとに読み取った工事内容の断片です。"
        "これらを踏まえて、見積書の冒頭に書く案件概要文を日本語で3〜6文にまとめてください。"
        "重複は整理し、矛盾がある場合は両論併記してください。JSON等は不要、日本語の文章のみを返してください。\n\n"
        f"{joined}"
    )
    try:
        response = await client.chat(
            "あなたは建設見積書の冒頭に置く案件概要文をまとめる担当です。",
            prompt,
            image_paths=None,
            max_tokens=800,
        )
        return response.text.strip()
    except VLMCallError as exc:
        logger.warning("project summary generation failed: %s", exc)
        return "（自動要約に失敗したため、各部分の読み取り結果を個別にご確認ください）"
