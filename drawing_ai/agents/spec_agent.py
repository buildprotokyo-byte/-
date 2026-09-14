"""COAI-01: 仕様書読解AI(基本仕様書AI).

Reads a specification-document tile -- 仕様書 itself, or any of its common
aliases (仕上げ表・内部仕上げ表・外部仕上げ表・仕様書一覧・特記仕様書・建具表
etc.; see prompts.OVERVIEW_USER_TEMPLATE) -- and extracts what a designer
packs into it: per-room finish/substrate/product facts, plus three fixed
"default questions" asked of every pass regardless of what is found (see
COAI_DEFINITIONS.md for the design conversation this implements). The user
singled this agent out as the highest-priority accuracy investment: a
well-read spec document narrows roughly 80% of a renovation's construction
scope before a single drawing symbol is interpreted, so it gets the same
treatment as Phase 1 (site facts) -- vector ground truth grounding +
ensemble self-consistency, not a single trusting pass.

Two extra techniques beyond the drawing-reading agents, both agreed on
explicitly rather than assumed:

1. **Table structure extracted first, semantics second.** Spec documents
   are almost always tables whose layout varies between design offices.
   Handing the model an already row-grouped transcription (see
   ``vector_extractor.extract_table_rows``, wired in via
   ``Tile.ground_truth_table_rows``) is far more robust than asking it to
   simultaneously parse an unfamiliar layout *and* interpret meaning.
2. **Terminology normalization** (terminology.py) maps a designer's
   free-text finish label to one canonical term where a mapping is known,
   while always keeping the raw text alongside it.

The three fixed default-question fields (basic info / scope-narrowing
terms / desired-change statements) are folded into Phase 1 and Phase 2's
existing pools rather than kept as a fourth, parallel structure -- basic
info is exactly SiteFact-shaped and gets the same ensemble reconciliation
Phase 1 uses (``site_agent.reconcile_site_facts``), desired-change
statements are exactly IntentStatement-shaped and join Phase 2's
statement pool. Only "scope-narrowing terms" has no existing home, since
it is unique to how specification documents work (see
``SpecTileResult.scope_target_terms``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .. import grounding
from ..config import settings
from ..prompts import SPEC_SYSTEM_PROMPT, SPEC_USER_TEMPLATE
from ..reference_resolution import resolve_references
from ..schemas import IntentStatement, QAItem, SiteFact, SpecItem, SpecProduct, SpecRoom, Tile
from ..terminology import normalize_term
from ..vlm_client import VLMCallError, extract_json, get_client
from .site_agent import reconcile_site_facts

logger = logging.getLogger("drawing_ai.agents.spec")

CONFIDENCE_REVIEW_THRESHOLD = 0.75


@dataclass
class SpecTileResult:
    rooms: list[SpecRoom] = field(default_factory=list)
    basic_facts: list[SiteFact] = field(default_factory=list)
    scope_target_terms: list[str] = field(default_factory=list)
    intent_statements: list[IntentStatement] = field(default_factory=list)
    qa_items: list[QAItem] = field(default_factory=list)


def _table_structure_context(tile: Tile) -> str:
    if not tile.ground_truth_table_rows:
        return "(テーブル構造は検出されませんでした。画像から直接判断してください)"
    return "\n".join(
        f"行{i + 1}: {' | '.join(row)}" for i, row in enumerate(tile.ground_truth_table_rows)
    )


async def _one_pass(tile: Tile) -> dict:
    client = get_client(settings.child_vlm)
    prompt = SPEC_USER_TEMPLATE.format(
        ocr_text=grounding.grounding_context(tile)[:2000],
        table_structure=_table_structure_context(tile),
    )
    response = await client.chat(
        SPEC_SYSTEM_PROMPT,
        prompt,
        image_paths=[tile.image_path],
        max_tokens=1600,
    )
    data = extract_json(response.text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object with rooms/basic_info_facts/...")
    return data


def _make_spec_item(raw: dict, tile: Tile) -> SpecItem:
    finish = str(raw.get("finish", "")).strip()
    product_raw = raw.get("product") or {}
    product = SpecProduct(
        manufacturer=product_raw.get("manufacturer"),
        model_number=product_raw.get("model_number"),
        category=str(product_raw.get("category", "")),
        looked_up_details=str(product_raw.get("looked_up_details", "")),
        lookup_status=str(product_raw.get("lookup_status", "not_attempted")),
    )

    delta, verified = grounding.text_adjustment(finish, tile)
    confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.7)) + delta))
    canonical = normalize_term(finish)

    return SpecItem(
        finish=canonical or finish,
        substrate=str(raw.get("substrate", "")),
        substrate_status=str(raw.get("substrate_status", "unknown")),
        notes=str(raw.get("notes", "")),
        in_scope=bool(raw.get("in_scope", True)),
        product=product,
        raw_finish_term=finish,
        canonical_finish_term=canonical,
        confidence=confidence,
        verified_by_vector=verified,
        needs_human_review=confidence < CONFIDENCE_REVIEW_THRESHOLD,
        source_tile_ids=[tile.tile_id],
    )


def _reconcile_rooms(passes: list[list[dict]], tile: Tile) -> list[SpecRoom]:
    rooms: dict[str, SpecRoom] = {}
    agreement_counts: dict[tuple[str, str, str], int] = {}

    for pass_rooms in passes:
        for raw_room in pass_rooms:
            room_name = str(raw_room.get("room_name", "")).strip()
            if not room_name:
                continue
            room = rooms.setdefault(room_name, SpecRoom(room_name=room_name, source_sheet_ids=[tile.sheet_id]))
            for raw_item in raw_room.get("specs", []) or []:
                item = _make_spec_item(raw_item, tile)
                key = (room_name, item.raw_finish_term, item.substrate)
                agreement_counts[key] = agreement_counts.get(key, 0) + 1
                existing = next(
                    (s for s in room.specs if (room_name, s.raw_finish_term, s.substrate) == key), None
                )
                if existing is None:
                    room.specs.append(item)
                elif item.confidence > existing.confidence:
                    room.specs[room.specs.index(existing)] = item

    # Independent agreement across ensemble passes is itself evidence --
    # mirrors site_agent's agreement_bonus treatment, since this agent gets
    # the same 100%-accuracy-bar redundancy weighting as Phase 1.
    for room in rooms.values():
        for item in room.specs:
            key = (room.room_name, item.raw_finish_term, item.substrate)
            count = agreement_counts.get(key, 1)
            if count > 1:
                item.confidence = min(1.0, item.confidence + 0.1 * (count - 1))
                item.needs_human_review = item.confidence < CONFIDENCE_REVIEW_THRESHOLD

    return list(rooms.values())


def _collect_intent_statements(passes: list[list[dict]], tile: Tile) -> list[IntentStatement]:
    statements: list[IntentStatement] = []
    for pass_items in passes:
        for raw in pass_items:
            text_ja = str(raw.get("text_ja", "")).strip()
            if not text_ja:
                continue
            statements.append(
                IntentStatement(
                    sheet_id=tile.sheet_id,
                    text_ja=text_ja,
                    construction_categories=[str(c) for c in raw.get("construction_categories", [])],
                    confidence=float(raw.get("confidence", 0.0)),
                    source_tile_ids=[tile.tile_id],
                )
            )
    return statements


def _collect_qa_items(passes: list[list[dict]], tile: Tile) -> list[QAItem]:
    """Dedupe raw質疑書 rows by item_no across ensemble passes, preferring
    the longer answer text (a truncated read losing a "質疑No.X参照" tail
    would otherwise silently become unresolvable downstream).

    Cross-reference resolution itself is deliberately *not* done here --
    a reference can point to a row read by a different tile, so it can
    only be resolved once every tile's rows are merged (see
    parent_agent.aggregate_spec).
    """
    best: dict[int | None, dict] = {}
    for pass_items in passes:
        for raw in pass_items:
            item_no = raw.get("item_no")
            answer = str(raw.get("answer", ""))
            current = best.get(item_no)
            if current is None or len(answer) > len(str(current.get("answer", ""))):
                best[item_no] = raw

    return [
        QAItem(
            item_no=raw.get("item_no"),
            category=str(raw.get("category", "")),
            question=str(raw.get("question", "")),
            answer=str(raw.get("answer", "")),
            resolved_answer=str(raw.get("answer", "")),
        )
        for raw in best.values()
        if str(raw.get("question", "")).strip() or str(raw.get("answer", "")).strip()
    ]


async def extract_spec(tile: Tile) -> SpecTileResult:
    ensemble_size = max(1, settings.spec_ensemble_size)
    results = await asyncio.gather(*(_one_pass(tile) for _ in range(ensemble_size)), return_exceptions=True)

    passes: list[dict] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("spec extraction pass failed for tile %s: %s", tile.tile_id, result)
            continue
        passes.append(result)

    if not passes:
        return SpecTileResult()

    room_passes = [p.get("rooms", []) or [] for p in passes]
    basic_fact_passes = [p.get("basic_info_facts", []) or [] for p in passes]
    intent_passes = [p.get("desired_change_statements", []) or [] for p in passes]
    qa_passes = [p.get("qa_items", []) or [] for p in passes]

    scope_terms: list[str] = []
    for p in passes:
        for term in p.get("scope_target_terms", []) or []:
            term = str(term).strip()
            if term and term not in scope_terms:
                scope_terms.append(term)

    return SpecTileResult(
        rooms=_reconcile_rooms(room_passes, tile),
        basic_facts=reconcile_site_facts(basic_fact_passes, tile) if any(basic_fact_passes) else [],
        scope_target_terms=scope_terms,
        intent_statements=_collect_intent_statements(intent_passes, tile),
        qa_items=_collect_qa_items(qa_passes, tile),
    )
