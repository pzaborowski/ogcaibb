"""No-op transport: discards chunks. Useful for offline mode and tests."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Ack, ChunkManifest

log = logging.getLogger(__name__)


class NoopTransport:
    name = "noop"

    async def send(self, chunk_path: Path, manifest: ChunkManifest) -> Ack:
        log.debug("noop transport discarding seq=%d (%s)", manifest.sequence, chunk_path)
        return Ack(accepted=manifest.record_count, duplicates=0, errors=[])

    async def aclose(self) -> None:
        return
