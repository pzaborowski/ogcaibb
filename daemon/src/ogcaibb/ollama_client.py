"""Thin async client around the remote Ollama HTTP API.

Hard rule: this client only talks to the configured Ollama host. There is no
fallback to OpenAI, Anthropic, or any other provider. If the host is unreachable
the daemon refuses requests with HTTP 503; do not paper over that.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


class OllamaUnavailable(RuntimeError):
    """Raised when the configured Ollama host is not reachable or unhealthy."""


class OllamaClient:
    def __init__(self, host: str | None = None, api_key: str | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.api_key = api_key or settings.ollama_api_key
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.host,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        """Verify the host is up and the configured models are loaded.

        Returns a dict with reachable/models on success; raises OllamaUnavailable otherwise.
        """
        try:
            r = await self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaUnavailable(f"Ollama host {self.host} unreachable: {e}") from e

        def _norm(name: str) -> str:
            # Ollama always reports tagged names ("foo:latest"); user config often
            # omits the tag. Normalise both sides before comparing.
            return name if ":" in name else f"{name}:latest"

        installed_raw = {m["name"] for m in r.json().get("models", [])}
        installed = {_norm(n) for n in installed_raw}
        wanted = {_norm(settings.model_chat), _norm(settings.model_embed)}
        missing = wanted - installed
        if missing:
            log.warning(
                "Configured models not yet pulled on %s: %s. "
                "Run `ollama pull <model>` on the server.",
                self.host,
                ", ".join(sorted(missing)),
            )
        return {"reachable": True, "host": self.host, "installed": sorted(installed)}

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Call /api/chat. Always returns a single dict (stream=False) for now."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"num_ctx": settings.num_ctx, **(options or {})},
        }
        if tools:
            payload["tools"] = tools
        try:
            r = await self._client.post("/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaUnavailable(f"Ollama chat call failed: {e}") from e
        return r.json()

    async def embed(self, model: str, text: str | list[str]) -> list[list[float]]:
        inputs = text if isinstance(text, list) else [text]
        try:
            r = await self._client.post("/api/embed", json={"model": model, "input": inputs})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaUnavailable(f"Ollama embed call failed: {e}") from e
        return r.json().get("embeddings", [])


_client: OllamaClient | None = None


def get_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
