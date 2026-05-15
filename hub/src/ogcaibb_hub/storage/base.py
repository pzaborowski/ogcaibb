"""Storage Protocols for the hub.

The hub keeps two separate stores so each can be swapped independently:

  * TraceStore  — raw gz JSONL chunks, content-addressed by (workstation, seq)
  * IndexStore  — per-trace metadata + ratings, queryable by trace_id

A vector store will join later as a third Protocol (`VectorStore.upsert/query`)
keyed by trace_id, but it is out of scope for v0.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class IngestSummary:
    accepted: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class TraceRow:
    trace_id: str
    workstation_id: str
    received_at: float
    model: str
    agent: str | None
    skill: str | None
    bytes_compressed: int


class TraceStore(Protocol):
    name: str

    async def put_chunk(
        self,
        *,
        workstation_id: str,
        sequence: int,
        sha256: str,
        body_gz: bytes,
    ) -> Path:
        """Persist the raw gzipped chunk. Returns the storage path."""
        ...

    async def chunk_exists(self, *, workstation_id: str, sequence: int) -> bool: ...


class IndexStore(Protocol):
    name: str

    async def upsert_trace(self, *, row: TraceRow) -> bool:
        """Insert a trace if new. Returns True for new, False for duplicate."""
        ...

    async def count_traces(self, *, workstation_id: str | None = None) -> int: ...

    async def close(self) -> None: ...
