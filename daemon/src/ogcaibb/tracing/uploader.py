"""Background task that drains sealed WAL chunks via a configured transport.

Lifecycle is owned by `main.lifespan` — `start()` returns an awaitable handle
that is `cancel()`ed at shutdown. Failures are logged and retried with
exponential backoff; non-retriable transport errors (auth, schema mismatch)
park the chunk so it isn't lost and surface a WARN every interval.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time

from .transport.base import ChunkManifest, TraceTransport, TransportError
from .wal import WAL

log = logging.getLogger(__name__)


class Uploader:
    def __init__(
        self,
        *,
        wal: WAL,
        transport: TraceTransport,
        workstation_id: str,
        schema_version: int,
        idle_interval: float = 30.0,
        min_backoff: float = 5.0,
        max_backoff: float = 300.0,
    ) -> None:
        self._wal = wal
        self._transport = transport
        self._workstation_id = workstation_id
        self._schema_version = schema_version
        self._idle = idle_interval
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ogcaibb-uploader")
        log.info(
            "trace uploader started (transport=%s, idle=%.0fs)",
            self._transport.name,
            self._idle,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        with contextlib.suppress(Exception):
            await self._transport.aclose()

    async def _run(self) -> None:
        backoff = self._min_backoff
        last_nonretriable_warn: float = 0.0
        while not self._stop.is_set():
            chunks = self._wal.list_sealed()
            if not chunks:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._idle)
                except asyncio.TimeoutError:
                    pass
                continue

            progress = False
            for chunk in chunks:
                if self._stop.is_set():
                    break
                manifest = ChunkManifest(
                    workstation_id=self._workstation_id,
                    sequence=chunk.seq,
                    sha256=chunk.sha256,
                    bytes_gz=chunk.bytes_gz,
                    record_count=chunk.record_count,
                    schema_version=self._schema_version,
                )
                try:
                    ack = await self._transport.send(chunk.path, manifest)
                except TransportError as e:
                    if e.retriable:
                        log.warning(
                            "transport error on seq=%d: %s — backing off %.1fs",
                            chunk.seq, e, backoff,
                        )
                        await self._sleep_with_stop(backoff)
                        backoff = min(self._max_backoff, backoff * 2)
                        backoff += random.uniform(0, backoff * 0.1)  # jitter
                        break  # retry from top of the chunk list
                    now = time.time()
                    if now - last_nonretriable_warn > 60:
                        log.error(
                            "non-retriable transport error on seq=%d: %s "
                            "(chunk parked, will retry next interval)",
                            chunk.seq, e,
                        )
                        last_nonretriable_warn = now
                    # Skip this chunk on this pass but don't drop it.
                    continue
                else:
                    log.info(
                        "uploaded seq=%d accepted=%d duplicates=%d",
                        chunk.seq, ack.accepted, ack.duplicates,
                    )
                    self._wal.mark_uploaded(chunk.seq)
                    progress = True
                    backoff = self._min_backoff

            if not progress:
                await self._sleep_with_stop(self._idle)

    async def _sleep_with_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
