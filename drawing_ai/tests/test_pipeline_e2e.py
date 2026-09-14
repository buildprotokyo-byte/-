"""End-to-end orchestrator run against a synthetic image and a fully mocked
VLM layer (see conftest.py). This exercises the real Phase 0->1->2->3
sequencing, tiling, OCR-fallback, dedup, and aggregation logic without
needing a GPU or a running open-source model server.
"""
from __future__ import annotations

import pytest
from PIL import Image

from .conftest import DEFAULT_DISPATCH, FakeVLMClient, build_synthetic_vector_pdf

fitz = pytest.importorskip("fitz")

from drawing_ai import config  # noqa: E402
from drawing_ai.orchestrator import run_pipeline  # noqa: E402
from drawing_ai.schemas import SheetType  # noqa: E402


@pytest.mark.asyncio
async def test_run_pipeline_end_to_end(tmp_path, monkeypatch, fake_clients):
    monkeypatch.setattr(config.settings, "tile_px", 150)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 20)
    monkeypatch.setattr(config.settings, "phase3_ensemble_size", 2)

    img_path = tmp_path / "site_plan.png"
    Image.new("RGB", (300, 220), color=(255, 255, 255)).save(img_path)

    result = await run_pipeline([str(img_path)], str(tmp_path / "work"))

    # Phase 0
    assert len(result.sheets) == 1
    assert result.sheets[0].sheet_type == SheetType.SITE_PLAN
    assert result.sheets[0].one_line_summary

    # Phase 1: the fake site agent always reports 敷地面積 with high
    # confidence, so it should show up deduped exactly once.
    assert any(f.key == "site_area_sqm" for f in result.phase1.facts)
    assert 0.0 <= result.phase1.completeness_score <= 1.0

    # Phase 2: at least one intent statement per tile, folded into a project
    # summary via the (mocked) parent LLM.
    assert result.phase2.statements
    assert result.phase2.project_summary_ja
    assert "大工工事" in result.phase2.inferred_construction_scope

    # Phase 3: dimensions/symbols/elements survive reconciliation, and the
    # vertical synthesis step ran (site_plan is treated as UNKNOWN-adjacent
    # so plan_elements may be empty, but the call must not crash).
    assert result.phase3.dimensions
    assert result.phase3.symbols
    assert result.phase3.elements
    assert 0.0 <= result.phase3.accuracy_estimate <= 1.0

    assert result.tile_count > 0
    assert result.model_calls > 0
    assert result.elapsed_s >= 0.0


@pytest.mark.asyncio
async def test_run_pipeline_survives_partial_agent_failures(tmp_path, monkeypatch, fake_clients):
    """If one child agent call raises, the rest of the pipeline must still
    produce a result instead of the whole run aborting."""
    monkeypatch.setattr(config.settings, "tile_px", 150)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 20)
    monkeypatch.setattr(config.settings, "phase3_ensemble_size", 1)

    call_count = {"n": 0}
    original_chat = fake_clients.chat

    async def flaky_chat(system_prompt, user_text, image_paths=None, **kwargs):
        call_count["n"] += 1
        if "寸法数字・線・記号" in system_prompt and call_count["n"] % 3 == 0:
            raise RuntimeError("simulated transient model failure")
        return await original_chat(system_prompt, user_text, image_paths, **kwargs)

    monkeypatch.setattr(fake_clients, "chat", flaky_chat)

    img_path = tmp_path / "site_plan.png"
    Image.new("RGB", (300, 220), color=(255, 255, 255)).save(img_path)

    result = await run_pipeline([str(img_path)], str(tmp_path / "work"))

    # The run must complete and still contain the sheets/phase1/phase2 output
    # that didn't hit the simulated failure.
    assert len(result.sheets) == 1
    assert result.phase2.statements


@pytest.mark.asyncio
async def test_run_pipeline_grounds_against_vector_pdf(tmp_path, monkeypatch, fake_clients):
    """When the source is a born-digital PDF, tiles should pick up exact
    ground-truth text, dimension readings that match it should be marked
    verified_by_vector, OCR should be skipped for those tiles, and the
    solid model should extract real wall/ceiling-height geometry -- none of
    which a plain raster image (the other tests here) can exercise."""
    monkeypatch.setattr(config.settings, "tile_px", 700)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 100)
    monkeypatch.setattr(config.settings, "phase3_ensemble_size", 1)

    pdf_path = tmp_path / "vector.pdf"
    build_synthetic_vector_pdf(str(pdf_path))

    result = await run_pipeline([str(pdf_path)], str(tmp_path / "work"))

    assert len(result.sheets) == 1

    # The fake VLM's canned Phase 3 response always reports "2,730" -- the
    # synthetic PDF's text layer contains the matching "2730", so this
    # reading must come back vector-verified.
    matching = [d for d in result.phase3.dimensions if d.raw_text == "2,730"]
    assert matching
    assert matching[0].verified_by_vector is True

    # The synthetic PDF has one CH=2500 label and one wall-colored filled
    # rectangle -- the solid model should pick up both, with the wall's
    # height matched to that label.
    assert result.phase3.solid_model.wall_segments
    assert result.phase3.solid_model.wall_segments[0].height_mm == pytest.approx(2500.0)


@pytest.mark.asyncio
async def test_run_pipeline_folds_spec_into_phase1_and_phase2(tmp_path, monkeypatch):
    """A sheet classified as `specification` should run through COAI-01
    (spec_agent), and its fixed-question output (basic info / desired
    changes) should be folded into Phase 1 / Phase 2's own pools -- not
    kept as an isolated fourth result -- while its per-room detail lands in
    result.spec."""
    monkeypatch.setattr(config.settings, "tile_px", 150)
    monkeypatch.setattr(config.settings, "tile_overlap_px", 20)
    monkeypatch.setattr(config.settings, "phase3_ensemble_size", 1)
    monkeypatch.setattr(config.settings, "spec_ensemble_size", 1)

    spec_dispatch = dict(DEFAULT_DISPATCH)
    spec_dispatch["一次確認担当"] = {
        **DEFAULT_DISPATCH["一次確認担当"],
        "sheet_type": "specification",
    }
    client = FakeVLMClient(spec_dispatch)

    for modpath in [
        "drawing_ai.agents.overview_agent",
        "drawing_ai.agents.site_agent",
        "drawing_ai.agents.intent_agent",
        "drawing_ai.agents.detail_agent",
        "drawing_ai.agents.vertical_synthesis_agent",
        "drawing_ai.agents.parent_agent",
        "drawing_ai.agents.spec_agent",
    ]:
        monkeypatch.setattr(f"{modpath}.get_client", lambda _endpoint, _c=client: _c)

    img_path = tmp_path / "spec_sheet.png"
    Image.new("RGB", (300, 220), color=(255, 255, 255)).save(img_path)

    result = await run_pipeline([str(img_path)], str(tmp_path / "work"))

    assert result.sheets[0].sheet_type == SheetType.SPECIFICATION

    # COAI-01's own detailed output.
    assert any(r.room_name == "洋室A" for r in result.spec.rooms)
    assert result.spec.scope_target_terms == ["2階居室のみ"]

    # Folded into Phase 1 (basic info) and Phase 2 (desired-change intent)
    # rather than kept separate.
    assert any(f.key == "building_structure" for f in result.phase1.facts)
    assert any("洋室" in s.text_ja for s in result.phase2.statements)
