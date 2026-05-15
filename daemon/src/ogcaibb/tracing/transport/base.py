"""Transport Protocol for shipping sealed chunks to the hub.

A transport accepts the **path** of a sealed `.jsonl.gz` chunk plus its manifest
and is responsible for the network/queue/whatever. It must be idempotent — the
uploader may retry the same chunk after a transient failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TransportError(RuntimeError):
    """Raised on transport failure. `retriable` hints to the uploader."""

    def __init__(self, message: str, *, retriable: bool = True) -> None:
        super().__init__(message)
        self.retriable = retriable


@dataclass
class ChunkManifest:
    workstation_id: str
    sequence: int
    sha256: str
    bytes_gz: int
    record_count: int
    schema_version: int


@dataclass
class Ack:
    accepted: int
    duplicates: int
    errors: list[str]


class TraceTransport(Protocol):
    name: str

    async def send(self, chunk_path: Path, manifest: ChunkManifest) -> Ack:
        """Deliver `chunk_path` to the hub. Raise TransportError on failure."""
        ...

    async def aclose(self) -> None:
        """Release any underlying resources (HTTP clients, queues)."""
        ...
