"""Ollama-backed embedder.

Calls the configured Ollama instance's `/api/embed` endpoint. On the target
deployment this is the GPU box reached via the private VLAN (no auth, no
TLS); locally it can also point at a developer's own Ollama for tests.

The `dim` is probed on first use rather than hardcoded so swapping the
embed model (nomic-embed-text → bge-m3 → mxbai-embed-large) doesn't need
a code change.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class OllamaEmbedder:
    name = "ollama"

    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        if not host:
            raise ValueError("OllamaEmbedder requires a host")
        if not model:
            raise ValueError("OllamaEmbedder requires a model name")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._host = host.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._host, headers=headers, timeout=timeout
        )
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            raise RuntimeError(
                "Embedder dim not yet known — call embed() at least once first."
            )
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        try:
            r = await self._client.post("/api/embed", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"Ollama embed call failed against {self._host}: {e}"
            ) from e
        vectors = r.json().get("embeddings") or []
        if vectors and self._dim is None:
            self._dim = len(vectors[0])
            log.info("Embedder %s probed dim=%d", self._model, self._dim)
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()
