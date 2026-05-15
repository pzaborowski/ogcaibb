"""SignalObserver.

Holds the set of enabled detectors and schedules them per captured turn.
Each detector runs in its own asyncio.Task after `detector.delay_seconds`
elapses; emitted Signals are appended to the WAL on the same code path the
explicit `/v1/feedback` endpoint uses.

The observer keeps strong references to its in-flight tasks so they survive
garbage collection, and cancels them cleanly during shutdown. It does not
persist pending detector intents across restarts: if the daemon stops while
detectors are waiting, those signals are simply lost (acceptable trade-off
for v0.1 — bringing the work back later means re-scheduling against the
WAL on startup, which is an obvious follow-up).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Iterable

from ..record import Trace
from ..wal import WAL
from .base import ImplicitSignal, SignalContext

log = logging.getLogger(__name__)


class SignalObserver:
    def __init__(
        self,
        *,
        wal: WAL,
        detectors: Iterable[ImplicitSignal],
        workstation_id: str,
        workspace_root: Path | None = None,
    ) -> None:
        self._wal = wal
        self._detectors: list[ImplicitSignal] = list(detectors)
        self._workstation_id = workstation_id
        self._workspace_root = workspace_root
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()

    @property
    def detectors(self) -> list[ImplicitSignal]:
        return list(self._detectors)

    def schedule(self, trace: Trace) -> None:
        """Spawn one task per enabled detector for the given (unredacted) trace."""
        if self._stop.is_set() or not self._detectors:
            return
        ctx = SignalContext(
            trace_id=trace.trace_id,
            workstation_id=trace.workstation_id,
            workspace_root=self._workspace_root,
            agent=trace.agent,
            skill=trace.skill,
            model=trace.model,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            assistant_text=trace.assistant_text,
            tool_calls=list(trace.tool_calls),
            error=trace.error,
            incomplete=trace.incomplete,
        )
        for detector in self._detectors:
            task = asyncio.create_task(
                self._run_detector(detector, ctx),
                name=f"signal:{detector.name}:{trace.trace_id[:8]}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        self._stop.set()
        pending = [t for t in self._tasks if not t.done()]
        for t in pending:
            t.cancel()
        for t in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()

    async def _run_detector(self, detector: ImplicitSignal, ctx: SignalContext) -> None:
        try:
            if detector.delay_seconds > 0:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=detector.delay_seconds
                    )
                    # observer is stopping — bail
                    return
                except asyncio.TimeoutError:
                    pass  # delay elapsed; proceed with detection
            signals = await detector.detect(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "implicit signal %s failed for trace %s: %s",
                detector.name, ctx.trace_id[:8], e,
            )
            return

        for sig in signals:
            try:
                self._wal.append_signal(sig)
            except Exception as e:
                log.warning("failed to append signal: %s", e)
