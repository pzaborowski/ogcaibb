"""Models for a single captured agent turn ("Trace") and feedback signals.

A Trace is one full /v1/chat/completions turn including any tool calls. It is
the unit of capture on the workstation and the unit of ingestion at the hub.
Both sides parse the same Pydantic model, so the schema is authoritative here.

Signals are emitted later by implicit/explicit feedback detectors and are
joined to traces by `trace_id` on the hub side.
"""

from __future__ import annotations

import os
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class ToolCallRecord(BaseModel):
    """A single tool invocation captured from PydanticAI's message trace."""

    kind: str  # "ToolCallPart" | "ToolReturnPart"
    data: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    """One full chat-completion turn."""

    schema_version: int = SCHEMA_VERSION
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    workstation_id: str
    daemon_version: str
    started_at: datetime
    ended_at: datetime
    agent: str | None = None
    skill: str | None = None
    model: str
    messages_in: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    assistant_text: str = ""
    reasoning: str | None = None
    incomplete: bool = False
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Signal(BaseModel):
    """A feedback signal attached to a previously-captured trace.

    `source` identifies the detector ("explicit", "git_commit", …).
    Polarity is -1/0/+1; weight is the detector's confidence in [0,1].
    """

    schema_version: int = SCHEMA_VERSION
    trace_id: str
    workstation_id: str
    source: str
    polarity: Literal[-1, 0, 1]
    weight: float = 1.0
    detected_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class WALRecord(BaseModel):
    """Union envelope written to disk; `kind` discriminates payload."""

    kind: Literal["trace", "signal"]
    payload: dict[str, Any]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_workstation_id(id_path: Path) -> str:
    """Return a stable UUID for this workstation, creating one on first call.

    Stored as plain text at `id_path`. Tied to the install, not the user — if
    the file is removed, a new identity is generated (treat as a new client).
    """
    id_path = id_path.expanduser()
    if id_path.exists():
        existing = id_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    id_path.parent.mkdir(parents=True, exist_ok=True)
    new_id = uuid.uuid4().hex
    id_path.write_text(new_id + "\n", encoding="utf-8")
    return new_id


def env_meta() -> dict[str, str]:
    """Lightweight environment context attached to every trace's meta block."""
    return {
        "platform": platform.system().lower(),
        "python": platform.python_version(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
    }
