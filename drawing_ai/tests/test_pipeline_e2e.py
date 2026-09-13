"""End-to-end orchestrator run against a synthetic image and a fully mocked
VLM layer (see conftest.py). This exercises the real Phase 0->1->2->3
sequencing, tiling, OCR-fallback, dedup, and aggregation logic without
needing a GPU or a running open-source model server.
"""
from __future__ import annotations

import pytest
from PIL import Image

from drawing_ai import config
from drawing_ai.orchestrator import run_pipeline
from drawing_ai.schemas import SheetType


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
