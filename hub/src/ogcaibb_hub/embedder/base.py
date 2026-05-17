"""Embedder Protocol.

Embedders sit between the hub and whatever produces text vectors. The
default impl proxies the GPU box's Ollama (`nomic-embed-text`), which the
hub reaches over its private VLAN; alternatives (sentence-transformers in
process, OpenAI-compatible endpoints, etc.) plug in by registering a new
builder.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int  # output vector dimensionality; vector store relies on this

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def aclose(self) -> None: ...
