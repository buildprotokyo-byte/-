"""Async client for OpenAI-compatible chat/vision endpoints.

Targets local, open-source inference servers (Ollama, LM Studio, vLLM,
text-generation-webui with the openai extension, ...) that all speak the
same `/v1/chat/completions` shape the existing BUILD PRO desktop app already
uses for its "外部AI" (LM Studio) integration. Nothing here is
provider-specific -- swapping ``VLMEndpoint.base_url``/``model`` in
``config.py`` is enough to change models.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import VLMEndpoint

logger = logging.getLogger("drawing_ai.vlm_client")

_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


@dataclass
class VLMResponse:
    text: str
    raw: dict[str, Any]


class VLMCallError(RuntimeError):
    pass


def _encode_image(path: str) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


class VLMClient:
    """One client per endpoint, with its own concurrency limiter.

    Call sites should get instances via :func:`get_client` so the semaphore
    (which enforces ``endpoint.max_concurrency``) is actually shared across
    the whole pipeline run instead of being reset per call.
    """

    def __init__(self, endpoint: VLMEndpoint) -> None:
        self.endpoint = endpoint
        self._semaphore = asyncio.Semaphore(max(1, endpoint.max_concurrency))
        self._client = httpx.AsyncClient(timeout=endpoint.timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        system_prompt: str,
        user_text: str,
        image_paths: Optional[list[str]] = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
        retries: int = 2,
    ) -> VLMResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for path in image_paths or []:
            b64 = _encode_image(path)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )

        payload = {
            "model": self.endpoint.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"

        url = self.endpoint.base_url.rstrip("/") + "/chat/completions"

        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                async with self._semaphore:
                    resp = await self._client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return VLMResponse(text=text, raw=data)
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning(
                    "VLM call failed (endpoint=%s attempt=%d/%d): %s",
                    self.endpoint.name,
                    attempt + 1,
                    retries + 1,
                    exc,
                )
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise VLMCallError(f"{self.endpoint.name} call failed after {retries + 1} attempts") from last_error


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response.

    Open-source models frequently wrap JSON in ```json fences or add a short
    preamble even when explicitly instructed not to; this tries a strict
    parse first and falls back to pulling the first {...}/[...] block.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"could not extract JSON from model response: {text[:200]!r}")


_clients: dict[str, VLMClient] = {}


def get_client(endpoint: VLMEndpoint) -> VLMClient:
    if endpoint.name not in _clients:
        _clients[endpoint.name] = VLMClient(endpoint)
    return _clients[endpoint.name]


async def close_all_clients() -> None:
    for client in _clients.values():
        await client.aclose()
    _clients.clear()
