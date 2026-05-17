"""VectorStore Protocol — pluggable vector backend keyed by trace_id.

We store one row per trace: the embedding of the trace's last user message
plus the metadata needed to compose useful exemplars on retrieve (the
message text, a snippet of the assistant response, agent/skill/model, and
the originating workstation_id).

A retrieval returns the top-k rows by cosine similarity, with the score the
caller can use for thresholding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class VectorRow:
    trace_id: str
    workstation_id: str
    vector: list[float]
    user_message: str
    assistant_text: str
    agent: str | None = None
    skill: str | None = None
    model: str = ""
    received_at: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedExemplar:
    trace_id: str
    score: float
    user_message: str
    assistant_text: str
    agent: str | None
    skill: str | None
    model: str


class VectorStore(Protocol):
    name: str

    async def upsert(self, *, row: VectorRow) -> None: ...

    async def query(
        self,
        *,
        vector: list[float],
        top_k: int = 5,
        filter_agent: str | None = None,
        filter_skill: str | None = None,
        filter_workstation: str | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedExemplar]: ...

    async def delete_by_trace(self, *, trace_id: str) -> None: ...

    async def count(self) -> int: ...

    async def aclose(self) -> None: ...
