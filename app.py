"""HTTP entrypoint for the drawing-reading pipeline (Phase 0-3).

This is a separate service from the existing BUILD PRO desktop app
(system/app.html in the V13.5.6 build). It is designed to be called from
that app's browser context (or from BUILD PRO OS's future 見積 tab) as an
"外部連携" API: point it at one or more drawing files, get back structured
Phase 1-3 results that a future Phase 4 (quantity take-off / pricing) layer
can consume.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

Then point it at whatever open-source OpenAI-compatible vision model server
is running locally (see drawing_ai/config.py for the DRAWING_AI_* env vars),
e.g. Ollama serving qwen2.5vl.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from drawing_ai.config import settings
from drawing_ai.orchestrator import run_pipeline
from drawing_ai.phase1_service import run_phase1
from drawing_ai.schemas import PipelineRun

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("drawing_ai.app")

app = FastAPI(
    title="BUILD PRO Drawing AI",
    description="図面自動読み取りパイプライン (フェーズ0〜3) — オープンソースAI連携版",
    version="0.1.0",
)

# Local/internal tool: permissive CORS so the existing BUILD PRO desktop app
# (a static HTML page opened from disk or a LAN URL) can call this API
# directly from the browser, mirroring how it already talks to LM Studio.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS_ROOT = Path(tempfile.gettempdir()) / "drawing_ai_runs"

ALLOWED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

MAX_FILES = 100
MAX_FILE_BYTES = 150 * 1024 * 1024  # matches the 150MB per-file limit already used by the desktop app


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "child_vlm": {
            "base_url": settings.child_vlm.base_url,
            "model": settings.child_vlm.model,
            "max_concurrency": settings.child_vlm.max_concurrency,
        },
        "parent_llm": {
            "base_url": settings.parent_llm.base_url,
            "model": settings.parent_llm.model,
        },
        "ocr_engine": settings.ocr_engine,
        "time_budget_s": settings.time_budget_s,
        "time_budget_target_s": settings.time_budget_target_s,
    }


@app.post("/analyze", response_model=PipelineRun)
async def analyze(files: list[UploadFile]) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="少なくとも1つの図面ファイルを送信してください。")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一度に処理できるのは最大{MAX_FILES}ファイルです。")

    run_id = uuid.uuid4().hex[:10]
    work_dir = RUNS_ROOT / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    try:
        for f in files:
            suffix = Path(f.filename or "").suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail=f"未対応の拡張子です: {f.filename}（対応: {', '.join(sorted(ALLOWED_SUFFIXES))}）",
                )
            dest = input_dir / (f.filename or f"upload-{len(saved_paths)}{suffix}")
            size = 0
            with dest.open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{f.filename}が150MBを超えています。",
                        )
                    out.write(chunk)
            saved_paths.append(str(dest))

        logger.info("run %s: analyzing %d file(s)", run_id, len(saved_paths))
        result = await run_pipeline(saved_paths, str(work_dir))
        return JSONResponse(content=result.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface pipeline errors to the caller
        logger.exception("run %s failed", run_id)
        raise HTTPException(status_code=500, detail=f"解析中にエラーが発生しました: {exc}") from exc
    finally:
        # Intermediate tiles/overview images are large and only useful for
        # debugging; drop them once the response is built. Comment this out
        # (or add a `?keep=1` flag) while tuning prompts/tiling, since the
        # per-tile images are the most useful debugging artifact.
        shutil.rmtree(work_dir, ignore_errors=True)


PHASE1_RUNS_ROOT = Path(tempfile.gettempdir()) / "drawing_ai_phase1_runs"


def _phase1_run_dir(run_id: str) -> Path:
    """Reject anything that isn't a bare run_id (defense against path
    traversal via a crafted run_id in a URL)."""
    if not run_id.isalnum():
        raise HTTPException(status_code=400, detail="不正なrun_idです。")
    d = PHASE1_RUNS_ROOT / run_id
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="指定のrunが見つかりません。")
    return d


@app.post("/phase1/analyze")
async def phase1_analyze(
    files: list[UploadFile],
    stop_after_flagged: int = 10,
    max_pages: int | None = None,
) -> JSONResponse:
    """Phase 1 only (文字・数字・記号の認識) -- no VLM server needed, unlike
    /analyze. See drawing_ai/phase1_service.py and README 3.13/3.14: real
    OCR + tile-overlap dedup + OCR-confidence-aware tiering, validated
    against 千倉相川邸.

    Unlike /analyze, the run's files (page images, per-word review data)
    are kept on disk (not deleted after the response) so the review UI can
    fetch page images and post human corrections against the same run_id
    afterward -- see GET /phase1/runs/{run_id}/pages/{page_key}.png and
    the /phase1/runs/{run_id}/review endpoints below.
    """
    if not files:
        raise HTTPException(status_code=400, detail="少なくとも1つの図面ファイルを送信してください。")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一度に処理できるのは最大{MAX_FILES}ファイルです。")

    run_id = uuid.uuid4().hex[:12]
    work_dir = PHASE1_RUNS_ROOT / run_id
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"未対応の拡張子です: {f.filename}（対応: {', '.join(sorted(ALLOWED_SUFFIXES))}）",
            )
        dest = input_dir / (f.filename or f"upload-{len(saved_paths)}{suffix}")
        size = 0
        with dest.open("wb") as out:
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    shutil.rmtree(work_dir, ignore_errors=True)
                    raise HTTPException(status_code=400, detail=f"{f.filename}が150MBを超えています。")
                out.write(chunk)
        saved_paths.append(str(dest))

    try:
        result = run_phase1(saved_paths, work_dir, stop_after_flagged=stop_after_flagged, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 - surface pipeline errors to the caller
        logger.exception("phase1 run %s failed", run_id)
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Phase1解析中にエラーが発生しました: {exc}") from exc

    payload = {
        "run_id": run_id,
        "ocr_engine": result.ocr_engine,
        "stopped_early": result.stopped_early,
        "stopped_after_page": result.stopped_after_page,
        "total_flagged": result.total_flagged,
        "pages": [
            {
                "page_key": p.page_key,
                "page_no": p.page_no,
                "image_url": f"/phase1/runs/{run_id}/pages/{p.page_key}.png",
                "width": p.width,
                "height": p.height,
                "words": p.words,
                "green_count": p.green_count,
                "amber_count": p.amber_count,
                "red_count": p.red_count,
            }
            for p in result.pages
        ],
    }
    (work_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return JSONResponse(content=payload)


@app.get("/phase1/runs/{run_id}/pages/{page_key}.png")
async def phase1_page_image(run_id: str, page_key: str) -> FileResponse:
    run_dir = _phase1_run_dir(run_id)
    if not page_key.isalnum():
        raise HTTPException(status_code=400, detail="不正なpage_keyです。")
    image_path = run_dir / f"page-{page_key}.png"
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="指定のページ画像が見つかりません。")
    return FileResponse(image_path, media_type="image/png")


@app.get("/phase1/runs/{run_id}/review")
async def phase1_get_review(run_id: str) -> dict:
    """Human review decisions recorded so far for this run: {word_id:
    {decision, corrected_text?, note?}}. Empty until the reviewer confirms
    anything."""
    run_dir = _phase1_run_dir(run_id)
    review_path = run_dir / "review.json"
    if not review_path.is_file():
        return {}
    return json.loads(review_path.read_text(encoding="utf-8"))


@app.post("/phase1/runs/{run_id}/review")
async def phase1_post_review(run_id: str, decisions: dict[str, dict]) -> dict:
    """Merge-save human review decisions. ``decisions`` is {word_id:
    {decision: "confirm"|"correct"|"flag", corrected_text?, note?}} --
    a reviewer only ever needs to send the words they actually acted on,
    not the whole page, so this merges into whatever was already saved
    rather than overwriting the file."""
    run_dir = _phase1_run_dir(run_id)
    review_path = run_dir / "review.json"
    existing = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
    existing.update(decisions)
    review_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return existing


@app.get("/")
async def root() -> dict:
    return {
        "service": "BUILD PRO Drawing AI",
        "endpoints": {
            "GET /health": "設定・接続先モデルの確認",
            "POST /analyze": "図面ファイル(複数可)をmultipart/form-dataで送信し、フェーズ0〜3の結果を取得(ローカルVLMサーバーが必要)",
            "POST /phase1/analyze": "図面ファイル(複数可)をmultipart/form-dataで送信し、Phase1(文字・数字・記号)のみを取得(VLM不要)",
            "GET /phase1/runs/{run_id}/pages/{page_key}.png": "Phase1実行で読み込んだページ画像を取得",
            "GET /phase1/runs/{run_id}/review": "そのrunの人間確認状況を取得",
            "POST /phase1/runs/{run_id}/review": "そのrunに人間確認結果を保存",
        },
    }
