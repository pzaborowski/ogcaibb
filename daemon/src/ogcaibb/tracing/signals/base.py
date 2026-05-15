"""ImplicitSignal Protocol + SignalContext.

A detector receives the **unredacted** trace data through `SignalContext`
because most detectors need full tool-call args (file paths, content) that
the on-disk redacted trace may have masked or truncated. The Signals they
emit are minimal numeric payloads; they don't pass through the redactor.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..record import Signal, ToolCallRecord


class SignalContext(BaseModel):
    """Everything a detector needs to inspect a completed turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    trace_id: str
    workstation_id: str
    workspace_root: Path | None = None
    agent: str | None = None
    skill: str | None = None
    model: str = ""
    started_at: datetime
    ended_at: datetime
    assistant_text: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    error: str | None = None
    incomplete: bool = False


class ImplicitSignal(Protocol):
    """Detect a feedback signal from a completed turn.

    Implementations should treat `detect()` as idempotent and side-effect
    free; the observer is in charge of scheduling, retries and Signal
    persistence.
    """

    name: str
    delay_seconds: float  # observer waits this long before invoking detect()

    async def detect(self, ctx: SignalContext) -> list[Signal]: ...
