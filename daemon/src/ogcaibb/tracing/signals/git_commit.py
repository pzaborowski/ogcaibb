"""Git-commit detector.

After a configurable delay, runs `git log --since=<trace.ended_at>` against
the files written/edited by the trace. A commit landing in that window for
any of the trace's files is a strong positive signal: the user actually
shipped the assistant's work.

We don't try to attribute the commit to a specific hunk — that would need
parsing diffs against the assistant's content and is brittle. The simpler
"commit touched this file after the turn finished" heuristic is good enough
to bootstrap a feedback dataset; ambiguous attributions can be re-labeled
later as the model improves.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..record import Signal, ToolCallRecord, utcnow
from .base import SignalContext
from .edit_retention import _extract_edits, _resolve_path

log = logging.getLogger(__name__)


class GitCommit:
    name = "git_commit"
    weight = 0.7

    def __init__(self, *, delay_seconds: float = 1800.0) -> None:
        self.delay_seconds: float = delay_seconds

    async def detect(self, ctx: SignalContext) -> list[Signal]:
        if ctx.workspace_root is None:
            return []
        if not (ctx.workspace_root / ".git").exists():
            return []

        edits = _extract_edits(ctx)
        if not edits:
            return []

        paths: list[Path] = []
        for edit in edits:
            resolved = _resolve_path(edit["path"], ctx.workspace_root)
            if resolved is None:
                continue
            paths.append(resolved)
        if not paths:
            return []

        # Use a small buffer behind ctx.ended_at so we capture commits made
        # immediately after the turn (clock skew, fast follow-up commits).
        since = ctx.ended_at.timestamp() - 5.0
        since_iso = datetime.fromtimestamp(since, tz=ctx.ended_at.tzinfo).isoformat()

        per_file: list[dict[str, Any]] = []
        any_commit = False
        for p in paths:
            try:
                rel = p.relative_to(ctx.workspace_root)
            except ValueError:
                continue
            commits = await _git_log_since(ctx.workspace_root, since_iso, str(rel))
            per_file.append({"path": str(rel), "commits": len(commits)})
            if commits:
                any_commit = True

        if not per_file:
            return []

        return [
            Signal(
                trace_id=ctx.trace_id,
                workstation_id=ctx.workstation_id,
                source=self.name,
                polarity=1 if any_commit else 0,
                weight=self.weight,
                detected_at=utcnow(),
                meta={"files": per_file, "since": since_iso},
            )
        ]


# --- helpers -----------------------------------------------------------------


async def _git_log_since(workspace_root: Path, since_iso: str, rel_path: str) -> list[str]:
    """Return commit hashes for `rel_path` since `since_iso`. Empty on any error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace_root),
            "log", f"--since={since_iso}", "--format=%H", "--", rel_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as e:
        log.debug("git_commit detector: git not available (%s)", e)
        return []
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []
    return [line for line in stdout.decode("utf-8", errors="replace").splitlines() if line]
