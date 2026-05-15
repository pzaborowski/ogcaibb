"""Edit-retention detector.

After a configurable delay, re-reads each file the assistant wrote or edited
during the turn and checks whether the content survived:

  Write tool : assistant's full content present in the file → +1
  Edit tool  : new_string substring present in the file    → +1
  File gone or both checks fail → -1
  Modified but still present → 0

Per-file results aggregate into a single Signal per detector run:
  all positives    → polarity = +1
  any negative     → polarity = -1
  mixed / neutrals → polarity = 0
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..record import Signal, ToolCallRecord, utcnow
from .base import SignalContext

log = logging.getLogger(__name__)


class EditRetention:
    name = "edit_retention"
    weight = 0.4

    def __init__(self, *, delay_seconds: float = 600.0) -> None:
        # exposed as ivar so the observer / tests can override.
        self.delay_seconds: float = delay_seconds

    async def detect(self, ctx: SignalContext) -> list[Signal]:
        edits = _extract_edits(ctx)
        if not edits:
            return []

        results: list[int] = []
        per_file: list[dict[str, Any]] = []
        for edit in edits:
            resolved = _resolve_path(edit["path"], ctx.workspace_root)
            if resolved is None:
                continue
            polarity = _check_retention(resolved, edit)
            results.append(polarity)
            per_file.append({"path": str(resolved), "polarity": polarity})

        if not results:
            return []

        if all(p == 1 for p in results):
            agg = 1
        elif any(p == -1 for p in results):
            agg = -1
        else:
            agg = 0

        return [
            Signal(
                trace_id=ctx.trace_id,
                workstation_id=ctx.workstation_id,
                source=self.name,
                polarity=agg,
                weight=self.weight,
                detected_at=utcnow(),
                meta={"files": per_file},
            )
        ]


# --- helpers -----------------------------------------------------------------


def _extract_edits(ctx: SignalContext) -> list[dict[str, Any]]:
    """Pull (tool, path, content/new_string) tuples from ToolCallParts."""
    edits: list[dict[str, Any]] = []
    for tc in ctx.tool_calls:
        if tc.kind != "ToolCallPart":
            continue
        data = tc.data or {}
        name = (data.get("tool_name") or "").lower()
        if name not in ("write", "edit"):
            continue
        args = _coerce_args(data.get("args"))
        path = (
            args.get("file_path")
            or args.get("filepath")
            or args.get("path")
            or args.get("filename")
        )
        if not isinstance(path, str) or not path:
            continue
        if name == "write":
            edits.append({"tool": "write", "path": path, "content": args.get("content", "")})
        else:
            edits.append({"tool": "edit", "path": path, "new_string": args.get("new_string", "")})
    return edits


def _coerce_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_path(path: str, workspace_root: Path | None) -> Path | None:
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace_root is not None:
        p = (workspace_root / p).resolve()
    else:
        p = p.resolve()
    if workspace_root is not None:
        try:
            p.relative_to(workspace_root.resolve())
        except ValueError:
            return None
    return p


def _check_retention(path: Path, edit: dict[str, Any]) -> int:
    """Return -1 / 0 / +1 for one file."""
    if not path.exists():
        return -1
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.debug("edit_retention read failed: %s", e)
        return 0

    if edit["tool"] == "write":
        original = edit.get("content", "") or ""
        if not original:
            return 0
        if current.strip() == original.strip():
            return 1
        if original.strip() and original.strip() in current:
            return 1
        # file was modified — neutral, not negative
        return 0

    # Edit tool: look for the new_string substring.
    new_string = edit.get("new_string", "") or ""
    if not new_string:
        return 0
    return 1 if new_string in current else -1
