"""Qdrant-backed vector store.

Lazy-imports `qdrant-client` so the dependency is only required when a
deployment actually selects this backend (`OGCAIBB_HUB_VECTOR_STORE=qdrant`).
Install with:

    pip install qdrant-client
    # and run Qdrant on the proxy box:
    docker run -d -p 6333:6333 -p 6334:6334 \
        -v /var/lib/ogcaibb-hub/qdrant:/qdrant/storage qdrant/qdrant

Collection schema:
  * one named collection per hub deployment (default: `ogcaibb-traces`)
  * point id = trace_id (UUID hex)
  * payload = {workstation_id, user_message, assistant_text, agent, skill,
               model, received_at}
"""

from __future__ import annotations

import logging
from typing import Any

from .base import RetrievedExemplar, VectorRow

log = logging.getLogger(__name__)


class QdrantVectorStore:
    name = "qdrant"

    def __init__(
        self,
        *,
        url: str,
        collection: str = "ogcaibb-traces",
        api_key: str | None = None,
    ) -> None:
        try:
            from qdrant_client import AsyncQdrantClient  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Qdrant backend selected but qdrant-client is not installed. "
                "Run: pip install qdrant-client"
            ) from e
        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._initialized = False

    async def _ensure_collection(self, dim: int) -> None:
        if self._initialized:
            return
        from qdrant_client.http.models import Distance, VectorParams  # type: ignore

        existing = {c.name for c in (await self._client.get_collections()).collections}
        if self._collection not in existing:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            log.info(
                "Created Qdrant collection %s (dim=%d, cosine)",
                self._collection, dim,
            )
        self._initialized = True

    async def upsert(self, *, row: VectorRow) -> None:
        if not row.vector:
            raise ValueError("VectorRow.vector cannot be empty")
        await self._ensure_collection(len(row.vector))
        from qdrant_client.http.models import PointStruct  # type: ignore

        payload: dict[str, Any] = {
            "workstation_id": row.workstation_id,
            "user_message": row.user_message,
            "assistant_text": row.assistant_text,
            "agent": row.agent,
            "skill": row.skill,
            "model": row.model,
            "received_at": row.received_at,
        }
        await self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=row.trace_id, vector=row.vector, payload=payload)],
        )

    async def query(
        self,
        *,
        vector: list[float],
        top_k: int = 5,
        filter_agent: str | None = None,
        filter_skill: str | None = None,
        filter_workstation: str | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedExemplar]:
        if not vector:
            return []
        await self._ensure_collection(len(vector))
        from qdrant_client.http.models import (  # type: ignore
            FieldCondition,
            Filter,
            MatchValue,
        )

        conditions = []
        if filter_agent is not None:
            conditions.append(FieldCondition(key="agent", match=MatchValue(value=filter_agent)))
        if filter_skill is not None:
            conditions.append(FieldCondition(key="skill", match=MatchValue(value=filter_skill)))
        if filter_workstation is not None:
            conditions.append(
                FieldCondition(key="workstation_id", match=MatchValue(value=filter_workstation))
            )
        qfilter = Filter(must=conditions) if conditions else None

        hits = await self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=top_k,
            query_filter=qfilter,
            score_threshold=min_score if min_score > 0 else None,
        )
        out: list[RetrievedExemplar] = []
        for h in hits:
            p = h.payload or {}
            out.append(
                RetrievedExemplar(
                    trace_id=str(h.id),
                    score=float(h.score),
                    user_message=str(p.get("user_message", "")),
                    assistant_text=str(p.get("assistant_text", "")),
                    agent=p.get("agent"),
                    skill=p.get("skill"),
                    model=str(p.get("model", "")),
                )
            )
        return out

    async def delete_by_trace(self, *, trace_id: str) -> None:
        from qdrant_client.http.models import PointIdsList  # type: ignore

        await self._client.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=[trace_id]),
        )

    async def count(self) -> int:
        info = await self._client.count(collection_name=self._collection, exact=False)
        return int(info.count)

    async def aclose(self) -> None:
        await self._client.close()
