"""Local embedding store backed by Chroma. Uses Ollama's embedding model."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from ..ollama_client import get_client
from .registry import register

log = logging.getLogger(__name__)

_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection
    import chromadb

    path = Path(settings.vector_path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))
    _collection = client.get_or_create_collection("ogcaibb")
    return _collection


@register(
    "EmbedUpsert",
    "Embed a document with Ollama and upsert it into the local vector store "
    "with the given id and optional metadata (JSON-encoded string).",
)
async def embed_upsert(doc_id: str, text: str, metadata_json: str = "{}") -> str:
    import json

    embeddings = await get_client().embed(settings.model_embed, text)
    if not embeddings:
        raise RuntimeError("Ollama returned no embeddings")
    _get_collection().upsert(
        ids=[doc_id],
        embeddings=embeddings,
        documents=[text],
        metadatas=[json.loads(metadata_json)],
    )
    return f"upserted {doc_id} dim={len(embeddings[0])}"


@register(
    "EmbedQuery",
    "Embed a query and return the top-k matching documents from the local store.",
)
async def embed_query(text: str, k: int = 5) -> str:
    embeddings = await get_client().embed(settings.model_embed, text)
    if not embeddings:
        raise RuntimeError("Ollama returned no embeddings")
    res = _get_collection().query(query_embeddings=embeddings, n_results=k)
    out = []
    for i, doc_id in enumerate(res["ids"][0]):
        out.append(f"{doc_id}\t{res['distances'][0][i]:.4f}\t{res['documents'][0][i][:200]}")
    return "\n".join(out) if out else "(no matches)"
