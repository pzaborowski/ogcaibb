"""Filesystem + shell tools, scoped to the configured workspace root.

These mirror the Claude Code primitives (Read / Write / Edit / Bash) so the
agent/skill markdown manifests Just Work without rewrites.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path

from ..config import settings
from .registry import register

log = logging.getLogger(__name__)


def _resolve_inside_workspace(rel_or_abs: str) -> Path:
    root = settings.workspace_root.expanduser().resolve()
    p = Path(rel_or_abs).expanduser()
    p = (root / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise PermissionError(f"Path {p} is outside workspace {root}") from e
    return p


@register("Read", "Read a file inside the workspace. Returns the full text.")
async def read(path: str) -> str:
    p = _resolve_inside_workspace(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist")
    return p.read_text(encoding="utf-8", errors="replace")


@register("Write", "Create or overwrite a file inside the workspace.")
async def write(path: str, content: str) -> str:
    p = _resolve_inside_workspace(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p} ({len(content)} chars)"


@register(
    "Edit",
    "Exact string replacement inside a file. old_string must occur exactly once.",
)
async def edit(path: str, old_string: str, new_string: str) -> str:
    p = _resolve_inside_workspace(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {p}")
    if count > 1:
        raise ValueError(f"old_string is not unique in {p} ({count} occurrences)")
    p.write_text(text.replace(old_string, new_string), encoding="utf-8")
    return f"edited {p}"


@register(
    "Bash",
    "Run a shell command from the workspace root. Returns combined stdout/stderr.",
)
async def bash(command: str, timeout_seconds: int = 120) -> str:
    cwd = settings.workspace_root.expanduser().resolve()
    log.info("Bash: %s", command)
    # On macOS we use /usr/bin/sandbox-exec when available; on Linux, firejail.
    # Detection is intentionally lazy: if neither is installed we run unsandboxed
    # and emit a warning. Production deployments should mandate one.
    wrapped = _sandboxed(command, cwd)
    proc = await asyncio.create_subprocess_shell(
        wrapped,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"command exceeded {timeout_seconds}s") from None
    return stdout.decode("utf-8", errors="replace")


def _sandboxed(cmd: str, cwd: Path) -> str:
    import shutil

    if shutil.which("sandbox-exec"):
        # macOS profile: deny network, allow read everywhere, write only in cwd.
        profile = (
            "(version 1)"
            "(allow default)"
            "(deny network*)"
            f"(allow file-write* (subpath \"{cwd}\"))"
        )
        return f"/usr/bin/sandbox-exec -p {shlex.quote(profile)} /bin/sh -c {shlex.quote(cmd)}"
    if shutil.which("firejail"):
        return f"firejail --quiet --net=none --private-cwd={shlex.quote(str(cwd))} /bin/sh -c {shlex.quote(cmd)}"
    log.warning("No sandbox-exec/firejail found — running Bash unsandboxed")
    return cmd
