"""TraceStore impl backed by the local filesystem.

Layout:

    <root>/<workstation_id>/<sequence:010d>.jsonl.gz

Chunks are written atomically via a temp file + rename. Existing chunks at the
same (workstation, sequence) are left untouched — duplicate uploads are no-ops
at the storage layer; dedupe at the trace level is the IndexStore's job.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class LocalFSTraceStore:
    name = "localfs"

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    async def put_chunk(
        self,
        *,
        workstation_id: str,
        sequence: int,
        sha256: str,
        body_gz: bytes,
    ) -> Path:
        ws_dir = self._root / _safe_id(workstation_id)
        ws_dir.mkdir(parents=True, exist_ok=True)
        target = ws_dir / f"{sequence:010d}.jsonl.gz"
        if target.exists():
            log.info("chunk already present; not overwriting: %s", target)
            return target
        tmp = target.with_suffix(".gz.partial")
        tmp.write_bytes(body_gz)
        os.replace(tmp, target)
        # sha256 is logged so the operator can grep for corruption forensics later.
        log.info(
            "stored chunk ws=%s seq=%d bytes=%d sha=%s",
            workstation_id, sequence, len(body_gz), sha256[:12],
        )
        return target

    async def chunk_exists(self, *, workstation_id: str, sequence: int) -> bool:
        p = self._root / _safe_id(workstation_id) / f"{sequence:010d}.jsonl.gz"
        return p.exists()


def _safe_id(s: str) -> str:
    """Defensive: only allow alnum/_- in path segments to avoid traversal."""
    return "".join(c for c in s if c.isalnum() or c in ("-", "_")) or "unknown"
