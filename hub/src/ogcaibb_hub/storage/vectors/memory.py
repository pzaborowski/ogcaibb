"""Dependency-free in-memory vector store.

Suitable for local dev and tests; for production deployments use the Qdrant
backend (selected via `OGCAIBB_HUB_VECTOR_STORE=qdrant`). Cosine similarity
is computed in pure Python — fine for thousands of vectors, too slow past
that. The store is not persisted; on hub restart it is empty.
"""

from __future__ import annotations

import math
from typing import Iterable

from .base import RetrievedExemplar, VectorRow


class InMemoryVectorStore:
    name = "memory"

    def __init__(self) -> None:
        self._rows: dict[str, VectorRow] = {}

    async def upsert(self, *, row: VectorRow) -> None:
        if not row.vector:
            raise ValueError("VectorRow.vector cannot be empty")
        self._rows[row.trace_id] = row

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
        if not vector or not self._rows:
            return []
        norm_q = _norm(vector)
        if norm_q == 0:
            return []
        scored: list[tuple[float, VectorRow]] = []
        for row in _filtered(
            self._rows.values(),
            filter_agent=filter_agent,
            filter_skill=filter_skill,
            filter_workstation=filter_workstation,
        ):
            score = _cosine(vector, row.vector, norm_q)
            if score < min_score:
                continue
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievedExemplar(
                trace_id=row.trace_id,
                score=score,
                user_message=row.user_message,
                assistant_text=row.assistant_text,
                agent=row.agent,
                skill=row.skill,
                model=row.model,
            )
            for score, row in scored[:top_k]
        ]

    async def delete_by_trace(self, *, trace_id: str) -> None:
        self._rows.pop(trace_id, None)

    async def count(self) -> int:
        return len(self._rows)

    async def aclose(self) -> None:
        return


def _filtered(
    rows: Iterable[VectorRow],
    *,
    filter_agent: str | None,
    filter_skill: str | None,
    filter_workstation: str | None,
) -> Iterable[VectorRow]:
    for r in rows:
        if filter_agent is not None and r.agent != filter_agent:
            continue
        if filter_skill is not None and r.skill != filter_skill:
            continue
        if filter_workstation is not None and r.workstation_id != filter_workstation:
            continue
        yield r


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine(q: list[float], r: list[float], norm_q: float) -> float:
    if len(q) != len(r):
        return 0.0
    norm_r = _norm(r)
    if norm_r == 0:
        return 0.0
    dot = sum(a * b for a, b in zip(q, r))
    return dot / (norm_q * norm_r)
