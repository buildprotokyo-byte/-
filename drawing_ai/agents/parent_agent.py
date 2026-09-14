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
   instead of losing all Phase 3 output. Readings already verified against
   the PDF's own vector/text layer (``verified_by_vector``) are held out of
   this step entirely -- they are ground truth, not something a model call
   should be able to rewrite, drop, or silently un-verify -- and are spliced
   back into the result untouched.
"""
from __future__ import annotations

import json
import logging
import re

from .. import grounding
from ..config import settings
from ..prompts import PARENT_AGGREGATE_SYSTEM_PROMPT, PARENT_AGGREGATE_USER_TEMPLATE
from ..reference_resolution import resolve_references
from ..schemas import (
    DimensionReading,
    ElementReading,
    IntentStatement,
    Phase1Result,
    Phase2Result,
    Phase3Result,
    QAItem,
    SiteFact,
    SpecResult,
    SpecRoom,
    SymbolReading,
    Tile,
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


def _rank(confidence: float, verified_by_vector: bool) -> tuple[bool, float]:
    """Sort key preferring a vector-verified reading over a merely
    higher-confidence one -- confidence already reflects verification via
    the grounding bonus, so this is mostly a tie-break, but it's an
    explicit safety net rather than relying on that bonus alone."""
    return (verified_by_vector, confidence)


def _dedupe_dimensions(items: list[DimensionReading], tile_sheet: dict[str, str]) -> list[DimensionReading]:
    best: dict[tuple[str, str], DimensionReading] = {}
    for d in items:
        key = _dim_key(d, tile_sheet)
        current = best.get(key)
        if current is None or _rank(d.confidence, d.verified_by_vector) > _rank(
            current.confidence, current.verified_by_vector
        ):
            best[key] = d
    return list(best.values())


def _dedupe_symbols(items: list[SymbolReading], tile_sheet: dict[str, str]) -> list[SymbolReading]:
    best: dict[tuple[str, str], SymbolReading] = {}
    for s in items:
        key = (tile_sheet.get(s.tile_id, ""), s.symbol_text_or_glyph.strip())
        current = best.get(key)
        if current is None or _rank(s.confidence, s.verified_by_vector) > _rank(
            current.confidence, current.verified_by_vector
        ):
            best[key] = s
    return list(best.values())


def _tiles_adjacent(a: Tile, b: Tile) -> bool:
    """Two tiles are "adjacent" (same or neighboring grid cell, including
    diagonals) if the overlap ingestion.py builds between neighboring
    tiles could plausibly have shown the same physical content twice."""
    return a.sheet_id == b.sheet_id and abs(a.row - b.row) <= 1 and abs(a.col - b.col) <= 1


def _dedupe_elements(
    items: list[ElementReading], tiles_by_id: dict[str, Tile] | None = None
) -> list[ElementReading]:
    """Collapse re-reads of the *same* physical element (seen again in an
    overlapping neighboring tile) without collapsing genuinely distinct
    elements that merely share a type and label.

    Grouping by (sheet_id, element_type, label) alone -- the original
    approach -- conflated these two cases: three separate downlights on
    one sheet, all labeled "ダウンライト", collapsed into a single
    ElementReading, silently undercounting the fixture. This is a real
    counting bug found by comparing pipeline output against an actual
    project's cost breakdown, not a hypothetical.

    ``tiles_by_id`` lets this additionally require that at least one pair
    of the two elements' source tiles are spatially adjacent (see
    ``_tiles_adjacent``) before merging them; elements from non-adjacent
    tiles are treated as distinct instances. When ``tiles_by_id`` is
    omitted (or a tile_id isn't in it), no adjacency information is
    available for that element, so it is never merged with anything --
    erring toward overcounting (a human re-checks a visible duplicate)
    rather than silently undercounting (a missing item is invisible).
    """
    tiles_by_id = tiles_by_id or {}
    groups: dict[tuple[str, str, str], list[ElementReading]] = {}
    for e in items:
        key = (e.sheet_id, e.element_type, e.label_ja.strip())
        if not key[2]:
            continue
        groups.setdefault(key, []).append(e)

    out: list[ElementReading] = []
    for group in groups.values():
        tiles_per_item = [[tiles_by_id[tid] for tid in e.tile_ids if tid in tiles_by_id] for e in group]

        parent = list(range(len(group)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if any(_tiles_adjacent(a, b) for a in tiles_per_item[i] for b in tiles_per_item[j]):
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for idx in range(len(group)):
            clusters.setdefault(find(idx), []).append(idx)

        for indices in clusters.values():
            cluster_items = [group[i] for i in indices]
            best = max(cluster_items, key=lambda e: _rank(e.confidence, e.verified_by_vector))
            merged = best.model_copy()
            merged.tile_ids = list({tid for e in cluster_items for tid in e.tile_ids})
            out.append(merged)

    return out


LOW_CONFIDENCE_THRESHOLD = 0.5


async def aggregate_phase3(
    all_dimensions: list[DimensionReading],
    all_symbols: list[SymbolReading],
    all_elements: list[ElementReading],
    vertical_notes: list[VerticalSynthesisNote],
    tile_sheet: dict[str, str],
    *,
    use_llm_reconciliation: bool = True,
    tiles_by_id: dict[str, Tile] | None = None,
) -> Phase3Result:
    dimensions = _dedupe_dimensions(all_dimensions, tile_sheet)
    symbols = _dedupe_symbols(all_symbols, tile_sheet)
    elements = _dedupe_elements(all_elements, tiles_by_id)

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
        # Vector-verified readings are ground truth (see grounding.py) --
        # they must never be rewritten, dropped, or have their
        # verified_by_vector flag silently lost by passing through a model
        # call that doesn't know to preserve it. Only the unverified
        # remainder, where a real conflict might exist, goes to the parent
        # LLM; verified readings are spliced back in untouched afterward.
        verified_dims = [d for d in dimensions if d.verified_by_vector]
        verified_syms = [s for s in symbols if s.verified_by_vector]
        verified_elems = [e for e in elements if e.verified_by_vector]
        unverified_dims = [d for d in dimensions if not d.verified_by_vector]
        unverified_syms = [s for s in symbols if not s.verified_by_vector]
        unverified_elems = [e for e in elements if not e.verified_by_vector]

        if unverified_dims or unverified_syms or unverified_elems:
            reconciled_dims, reconciled_syms, reconciled_elems, extra_flags = await _llm_reconcile(
                unverified_dims, unverified_syms, unverified_elems
            )
            low_confidence_flags.extend(extra_flags)
        else:
            reconciled_dims, reconciled_syms, reconciled_elems = [], [], []
        dimensions = verified_dims + reconciled_dims
        symbols = verified_syms + reconciled_syms
        elements = verified_elems + reconciled_elems

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
    "gross_footprint_sqm": "延床面積(概算)",
}

# How far apart two numeric readings of "the same fact" may sit and still
# be treated as agreement rather than a genuine disagreement worth
# flagging. Deliberately generous -- this compares readings that may come
# from entirely different source documents/sheets (see
# vector_extractor.estimate_gross_footprint's docstring on why a fact is
# looked for across every sheet, not just the "obviously relevant" one),
# so exact match isn't expected the way it is within one sheet's own
# dimension-chain self-check.
_DISAGREEMENT_TOLERANCE_PCT = 0.10


def _numeric_or_none(value: str) -> float | None:
    try:
        return float(grounding.normalize_number(value))
    except (TypeError, ValueError):
        return None


def _values_disagree(a: str, b: str) -> bool:
    na, nb = _numeric_or_none(a), _numeric_or_none(b)
    if na is not None and nb is not None:
        if na == 0 and nb == 0:
            return False
        return abs(na - nb) > _DISAGREEMENT_TOLERANCE_PCT * max(abs(na), abs(nb))
    return a.strip() != b.strip()


def aggregate_phase1(all_facts: list[SiteFact]) -> Phase1Result:
    """Dedupe site facts by key, preferring the highest-confidence reading.

    Facts for the same key can legitimately arrive from multiple, entirely
    different source documents (a floor plan's own dimension chain, a
    spec-document basic-info fact, a footprint estimate off an unrelated
    MEP sheet that happens to carry the same outer dimension). This is
    exactly the cross-source redundancy a human estimator uses to raise
    confidence when sources agree -- and to flag a genuine discrepancy
    worth a human's attention, rather than silently discarding it, when
    they don't.
    """
    best: dict[str, SiteFact] = {}
    disagreement_flags: list[str] = []

    for fact in all_facts:
        current = best.get(fact.key)
        if current is None:
            best[fact.key] = fact
            continue

        disagrees = _values_disagree(current.value, fact.value)
        if _rank(fact.confidence, fact.verified_by_vector) > _rank(
            current.confidence, current.verified_by_vector
        ):
            if disagrees:
                disagreement_flags.append(
                    f"「{fact.label_ja or fact.key}」で情報源ごとに値が食い違いました: "
                    f"{current.value}(confidence={current.confidence:.2f}, "
                    f"sheets={current.source_sheet_ids}) と "
                    f"{fact.value}(confidence={fact.confidence:.2f}, sheets={fact.source_sheet_ids})。"
                    f"確信度の高い{fact.value}を採用しましたが要確認。"
                )
            best[fact.key] = fact
        elif fact.confidence == current.confidence:
            if disagrees:
                disagreement_flags.append(
                    f"「{fact.label_ja or fact.key}」で同程度の確信度の情報源同士が食い違いました: "
                    f"{current.value} vs {fact.value}。両方とも要確認。"
                )
            else:
                # Independent agreement across tiles/sheets on the same
                # fact is itself evidence -- merge sources and nudge
                # confidence up.
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
    unresolved += disagreement_flags

    return Phase1Result(facts=facts, completeness_score=completeness, unresolved_questions=unresolved)


def _merge_and_resolve_qa_items(all_qa_items: list[QAItem]) -> list[QAItem]:
    """Dedupe 質疑書 rows by item_no across every tile/sheet they were read
    from, then resolve "質疑No.X参照"-style cross-references once over the
    complete merged set -- a reference can point to a row read from a
    different tile than the one containing it, so this can only happen
    after every tile's rows are merged (see reference_resolution.py for
    why this specific failure mode matters: a real blind test produced a
    wrong answer from exactly an unresolved reference)."""
    best: dict[int | None, QAItem] = {}
    for item in all_qa_items:
        current = best.get(item.item_no)
        if current is None or len(item.answer) > len(current.answer):
            best[item.item_no] = item

    raw = [
        {"item_no": item.item_no, "category": item.category, "question": item.question, "answer": item.answer}
        for item in best.values()
    ]
    resolved = resolve_references(raw, number_key="item_no", answer_key="answer")

    out = []
    for original, r in zip(best.values(), resolved):
        out.append(
            QAItem(
                item_no=original.item_no,
                category=original.category,
                question=original.question,
                answer=original.answer,
                resolved_answer=str(r["answer"]),
                resolved_from_item_no=r.get("resolved_from_reference"),
                unresolved_chained_reference=r.get("unresolved_chained_reference"),
            )
        )
    return out


_WH_DIMENSION_PATTERN = re.compile(r"W\s*(\d{3,5})\s*[×xX]\s*H\s*(\d{3,5})")


def check_spec_dimensions_against_drawing(spec: SpecResult, phase3: Phase3Result) -> list[str]:
    """Flag a W×H dimension stated in a spec/QA document that doesn't
    match any vector-verified dimension actually printed on the drawing.

    This resolves COAI-01's previously-open design question ("図面との
    矛盾時の扱い"): a blind test against a real project trusted a
    質疑書-stated sliding-wall size (W3640×H2400) over the drawing's own
    vector-confirmed dimension for the same opening (W3548×H2035) with no
    check at all, and the drawing turned out to be the one that matched
    the final specification. The policy this implements: the drawing's
    own vector-verified text is treated as more authoritative for
    physical dimensions specifically (a spec document can describe a
    since-changed or preliminary figure), while both readings are always
    kept and surfaced -- never silently dropped -- since a spec figure
    can also be the one that's right (e.g. before a drawing is updated).
    """
    verified_numbers = {
        grounding.normalize_number(d.raw_text) for d in phase3.dimensions if d.verified_by_vector
    }
    if not verified_numbers:
        return []

    texts = [item.resolved_answer for item in spec.qa_items]
    texts += [spec_item.notes for room in spec.rooms for spec_item in room.specs]

    flags: list[str] = []
    for text in texts:
        for m in _WH_DIMENSION_PATTERN.finditer(text or ""):
            width, height = m.group(1), m.group(2)
            if width not in verified_numbers and height not in verified_numbers:
                flags.append(
                    f"仕様書等に記載の寸法「W{width}×H{height}」は、図面のベクター確定寸法の"
                    f"どれとも一致しません。図面の実測値(ベクター確定)を優先してください。"
                    f"仕様書記載時点からの仕様変更の可能性もあるため、要確認として両方を記録します。"
                )
    return flags


def aggregate_spec(
    all_rooms: list[SpecRoom],
    scope_target_terms: list[str] | None = None,
    all_qa_items: list[QAItem] | None = None,
) -> SpecResult:
    """Merge per-tile SpecRoom lists (COAI-01, one call per spec-sheet tile)
    into one result, combining spec items for the same room found across
    multiple/overlapping tiles.

    A within-room disagreement on substrate_status (existing vs. new) found
    across different tiles is exactly the "図面との矛盾" case COAI-01's
    design conversation flagged as still open -- rather than silently
    picking one side, it is recorded in ``ambiguous_flags`` and the
    higher-confidence reading is kept, per the tentative "図面優先だが
    矛盾はフラグを立てて両方残す" default noted in COAI_DEFINITIONS.md.
    """
    rooms: dict[str, SpecRoom] = {}
    ambiguous_flags: list[str] = []

    for room in all_rooms:
        existing = rooms.get(room.room_name)
        if existing is None:
            rooms[room.room_name] = room.model_copy(deep=True)
            continue
        existing.source_sheet_ids = list(set(existing.source_sheet_ids) | set(room.source_sheet_ids))
        for item in room.specs:
            key = (item.raw_finish_term, item.substrate)
            match = next((s for s in existing.specs if (s.raw_finish_term, s.substrate) == key), None)
            if match is None:
                existing.specs.append(item)
                continue
            if (
                match.substrate_status != item.substrate_status
                and "unknown" not in (match.substrate_status, item.substrate_status)
            ):
                ambiguous_flags.append(
                    f"「{room.room_name}」の「{item.raw_finish_term}」で下地の既存/新規判定が"
                    f"タイル間で食い違いました({match.substrate_status} vs {item.substrate_status})。要確認。"
                )
            if item.confidence > match.confidence:
                existing.specs[existing.specs.index(match)] = item

    review_count = sum(1 for room in rooms.values() for item in room.specs if item.needs_human_review)
    total_items = sum(len(room.specs) for room in rooms.values())

    deduped_terms: list[str] = []
    for term in scope_target_terms or []:
        if term not in deduped_terms:
            deduped_terms.append(term)

    return SpecResult(
        rooms=list(rooms.values()),
        qa_items=_merge_and_resolve_qa_items(all_qa_items or []),
        scope_target_terms=deduped_terms,
        ambiguous_flags=ambiguous_flags,
        completeness_note=f"{len(rooms)}部屋・{total_items}項目を読み取りました。要確認{review_count}件。",
    )


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
