"""Runtime configuration.

Everything here is overridable via environment variables so the same code can
point at whatever OpenAI-compatible open-source inference server the operator
has running (Ollama, LM Studio, vLLM, text-generation-webui, ...). This
mirrors the connection pattern already used by the existing BUILD PRO desktop
app (LM Studio / OpenAI-compatible API at http://127.0.0.1:1234/v1), so the
two systems can eventually share one local model server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class VLMEndpoint:
    """One OpenAI-compatible chat/vision endpoint."""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    timeout_s: float = 120.0
    max_concurrency: int = 4


@dataclass
class Settings:
    # Small/fast model used for the Phase 0 overview pass and for the many
    # parallel Phase 3 child agents (one per tile). This is meant to be a
    # compact open-source vision-language model, e.g. served via Ollama:
    #   ollama run qwen2.5vl:7b
    child_vlm: VLMEndpoint = field(
        default_factory=lambda: VLMEndpoint(
            name="child",
            base_url=os.environ.get("DRAWING_AI_CHILD_BASE_URL", "http://127.0.0.1:11434/v1"),
            model=os.environ.get("DRAWING_AI_CHILD_MODEL", "qwen2.5vl:7b"),
            api_key=os.environ.get("DRAWING_AI_CHILD_API_KEY", ""),
            timeout_s=_env_float("DRAWING_AI_CHILD_TIMEOUT_S", 90.0),
            max_concurrency=_env_int("DRAWING_AI_CHILD_CONCURRENCY", 6),
        )
    )

    # Larger/slower model used once by the parent agent to aggregate all
    # child outputs into the final structured result. Only needs to reason
    # over text (the child agents already converted pixels to structured
    # JSON + OCR text), so it does not need vision.
    parent_llm: VLMEndpoint = field(
        default_factory=lambda: VLMEndpoint(
            name="parent",
            base_url=os.environ.get("DRAWING_AI_PARENT_BASE_URL", "http://127.0.0.1:11434/v1"),
            model=os.environ.get("DRAWING_AI_PARENT_MODEL", "qwen2.5:32b"),
            api_key=os.environ.get("DRAWING_AI_PARENT_API_KEY", ""),
            timeout_s=_env_float("DRAWING_AI_PARENT_TIMEOUT_S", 180.0),
            max_concurrency=_env_int("DRAWING_AI_PARENT_CONCURRENCY", 1),
        )
    )

    # Tiling parameters. A ~100,000-character-equivalent drawing set (many
    # sheets, dense annotation) is broken into many small tiles so each
    # child-agent call stays within a small open-source VLM's reliable
    # context/resolution budget instead of asking one model to "read
    # everything at once" (which is where accuracy collapses).
    tile_px: int = _env_int("DRAWING_AI_TILE_PX", 1024)
    tile_overlap_px: int = _env_int("DRAWING_AI_TILE_OVERLAP_PX", 160)
    render_dpi: int = _env_int("DRAWING_AI_RENDER_DPI", 220)

    # Whole-pipeline wall-clock budget. The orchestrator uses this to decide
    # how aggressively to parallelize / when to fall back to a faster model
    # tier so that a ~30万円クラス (roughly 100k-character-dense) drawing set
    # finishes within 15–30 minutes as required.
    time_budget_s: int = _env_int("DRAWING_AI_TIME_BUDGET_S", 1800)
    time_budget_target_s: int = _env_int("DRAWING_AI_TIME_BUDGET_TARGET_S", 900)

    ocr_engine: str = os.environ.get("DRAWING_AI_OCR_ENGINE", "paddleocr")
    ocr_lang: str = os.environ.get("DRAWING_AI_OCR_LANG", "japan")

    # Ensemble: how many independent child reads to run per tile.
    # Phase 1 gets the largest ensemble of any phase -- it has no downstream
    # phase left to catch a mistake, and its explicit target is 100%
    # accuracy, so redundancy is weighted most heavily there. Phase 3 also
    # runs an ensemble (majority-vote / disagreement-flagging improves
    # precision on small numerals and symbols far more than a single pass
    # does); Phase 2 (prose intent) does not, since there's no crisp
    # "agree/disagree" signal for a free-text sentence to vote on.
    phase1_ensemble_size: int = _env_int("DRAWING_AI_PHASE1_ENSEMBLE", 3)
    phase3_ensemble_size: int = _env_int("DRAWING_AI_PHASE3_ENSEMBLE", 3)


settings = Settings()
