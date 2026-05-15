"""Append-only write-ahead log for trace records.

Layout under `trace_dir`:

    open-<seq>.jsonl         currently-appending chunk (one per WAL)
    sealed-<seq>.jsonl.gz    sealed, awaiting upload
    manifest.db              SQLite: per-chunk state, sha256, counts
    .next-seq                monotonic counter

Rotation triggers on size (`max_bytes`) or age (`max_age_seconds`). On startup,
any open chunk older than `max_age_seconds` is sealed; partial last lines are
truncated. Sealed chunks survive restarts and are picked up by the uploader.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .record import Signal, Trace, WALRecord

log = logging.getLogger(__name__)


@dataclass
class ChunkInfo:
    seq: int
    path: Path           # path to the sealed .jsonl.gz on disk
    sha256: str
    bytes_gz: int
    record_count: int
    sealed_at: float     # unix ts


class WAL:
    """Single-writer append-only log with size/time rotation.

    Not thread-safe. The daemon writes from one asyncio loop on a single
    thread, so a coarse asyncio.Lock around `append()` is enough — held by
    the caller, not by this class, to keep the WAL synchronous and testable.
    """

    def __init__(
        self,
        trace_dir: Path,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        max_age_seconds: float = 300.0,
        max_total_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self.dir = trace_dir.expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_age_seconds = max_age_seconds
        self.max_total_bytes = max_total_bytes

        self._seq_path = self.dir / ".next-seq"
        self._manifest_path = self.dir / "manifest.db"
        self._db = sqlite3.connect(self._manifest_path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                seq INTEGER PRIMARY KEY,
                state TEXT NOT NULL,             -- open | sealed | uploaded
                sha256 TEXT,
                bytes_gz INTEGER,
                record_count INTEGER,
                sealed_at REAL,
                uploaded_at REAL
            );
            """
        )
        self._db.commit()

        self._open_seq: int | None = None
        self._open_path: Path | None = None
        self._open_fp: io.TextIOBase | None = None
        self._open_started: float = 0.0
        self._open_bytes: int = 0
        self._open_count: int = 0

        self._recover_open_chunks()

    # --- public surface -------------------------------------------------

    def append_trace(self, trace: Trace) -> None:
        self._append(WALRecord(kind="trace", payload=trace.model_dump(mode="json")))

    def append_signal(self, signal: Signal) -> None:
        self._append(WALRecord(kind="signal", payload=signal.model_dump(mode="json")))

    def list_sealed(self) -> list[ChunkInfo]:
        rows = self._db.execute(
            "SELECT seq, sha256, bytes_gz, record_count, sealed_at "
            "FROM chunks WHERE state = 'sealed' ORDER BY seq"
        ).fetchall()
        out: list[ChunkInfo] = []
        for seq, sha256, bytes_gz, count, sealed_at in rows:
            p = self._sealed_path(seq)
            if not p.exists():
                # disk and manifest diverged — drop the row
                self._db.execute("DELETE FROM chunks WHERE seq=?", (seq,))
                self._db.commit()
                continue
            out.append(ChunkInfo(seq, p, sha256, bytes_gz, count, sealed_at))
        return out

    def mark_uploaded(self, seq: int) -> None:
        p = self._sealed_path(seq)
        if p.exists():
            p.unlink()
        self._db.execute(
            "UPDATE chunks SET state='uploaded', uploaded_at=? WHERE seq=?",
            (time.time(), seq),
        )
        self._db.commit()
        # Garbage-collect old uploaded rows to keep manifest small.
        self._db.execute(
            "DELETE FROM chunks WHERE state='uploaded' "
            "AND uploaded_at < ?", (time.time() - 86400,),
        )
        self._db.commit()

    def stats(self) -> dict[str, int]:
        sealed = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(bytes_gz),0) FROM chunks WHERE state='sealed'"
        ).fetchone()
        uploaded = self._db.execute(
            "SELECT COUNT(*) FROM chunks WHERE state='uploaded'"
        ).fetchone()
        return {
            "sealed_chunks": int(sealed[0]),
            "sealed_bytes_gz": int(sealed[1]),
            "uploaded_chunks": int(uploaded[0]),
            "open_chunk_seq": self._open_seq or -1,
            "open_chunk_bytes": self._open_bytes,
            "open_chunk_records": self._open_count,
        }

    def seal_now(self) -> int | None:
        """Force-seal the current open chunk. Returns the sealed seq or None."""
        if self._open_seq is None:
            return None
        return self._seal_open()

    def close(self) -> None:
        try:
            self.seal_now()
        finally:
            if self._open_fp:
                self._open_fp.close()
                self._open_fp = None
            self._db.close()

    # --- internals ------------------------------------------------------

    def _append(self, record: WALRecord) -> None:
        self._maybe_open()
        line = json.dumps(record.model_dump(mode="json"), separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        assert self._open_fp is not None
        self._open_fp.write(line)
        self._open_fp.flush()
        os.fsync(self._open_fp.fileno())
        self._open_bytes += len(encoded)
        self._open_count += 1
        if self._should_rotate():
            self._seal_open()

    def _maybe_open(self) -> None:
        if self._open_fp is not None:
            return
        seq = self._allocate_seq()
        path = self._open_path_for(seq)
        fp = open(path, "a", encoding="utf-8")
        self._open_seq = seq
        self._open_path = path
        self._open_fp = fp
        self._open_started = time.time()
        self._open_bytes = path.stat().st_size if path.exists() else 0
        self._open_count = self._count_lines(path) if path.exists() else 0
        self._db.execute(
            "INSERT OR IGNORE INTO chunks(seq, state) VALUES(?, 'open')",
            (seq,),
        )
        self._db.commit()
        log.debug("WAL opened chunk seq=%d at %s", seq, path)

    def _should_rotate(self) -> bool:
        if self._open_bytes >= self.max_bytes:
            return True
        if time.time() - self._open_started >= self.max_age_seconds:
            return True
        return False

    def _seal_open(self) -> int | None:
        if self._open_seq is None or self._open_path is None or self._open_fp is None:
            return None
        seq = self._open_seq
        path = self._open_path
        try:
            self._open_fp.flush()
            os.fsync(self._open_fp.fileno())
        finally:
            self._open_fp.close()
        self._open_fp = None

        if not path.exists() or path.stat().st_size == 0:
            # Nothing to seal; just drop the row.
            self._db.execute("DELETE FROM chunks WHERE seq=?", (seq,))
            self._db.commit()
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self._reset_open_state()
            return None

        sealed_path = self._sealed_path(seq)
        sha = hashlib.sha256()
        # Gzip in one pass; computes sha256 of the gzipped bytes for transport.
        with open(path, "rb") as src, gzip.open(sealed_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=64 * 1024)
        gz_bytes = sealed_path.read_bytes()
        sha.update(gz_bytes)
        sealed_at = time.time()
        self._db.execute(
            "UPDATE chunks SET state='sealed', sha256=?, bytes_gz=?, "
            "record_count=?, sealed_at=? WHERE seq=?",
            (sha.hexdigest(), len(gz_bytes), self._open_count, sealed_at, seq),
        )
        self._db.commit()
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        log.info(
            "WAL sealed chunk seq=%d records=%d bytes_gz=%d",
            seq, self._open_count, len(gz_bytes),
        )
        self._reset_open_state()
        self._enforce_disk_budget()
        return seq

    def _reset_open_state(self) -> None:
        self._open_seq = None
        self._open_path = None
        self._open_started = 0.0
        self._open_bytes = 0
        self._open_count = 0

    def _recover_open_chunks(self) -> None:
        """Salvage any 'open' chunk from a previous run.

        We do NOT resume appending into a recovered chunk — sealing it
        eagerly keeps file rotation predictable and simplifies the uploader.
        Partial last lines are truncated.
        """
        rows = self._db.execute(
            "SELECT seq FROM chunks WHERE state='open' ORDER BY seq"
        ).fetchall()
        for (seq,) in rows:
            p = self._open_path_for(seq)
            if not p.exists():
                self._db.execute("DELETE FROM chunks WHERE seq=?", (seq,))
                self._db.commit()
                continue
            self._truncate_partial_last_line(p)
            self._open_seq = seq
            self._open_path = p
            self._open_fp = open(p, "a", encoding="utf-8")
            self._open_started = time.time() - self.max_age_seconds - 1  # force rotate
            self._open_bytes = p.stat().st_size
            self._open_count = self._count_lines(p)
            self._seal_open()

    def _enforce_disk_budget(self) -> None:
        total = sum(
            r[0] or 0
            for r in self._db.execute(
                "SELECT bytes_gz FROM chunks WHERE state='sealed'"
            ).fetchall()
        )
        if total <= self.max_total_bytes:
            return
        log.warning(
            "WAL disk budget exceeded (%d > %d); dropping oldest sealed chunks",
            total, self.max_total_bytes,
        )
        rows = self._db.execute(
            "SELECT seq, bytes_gz FROM chunks WHERE state='sealed' ORDER BY seq"
        ).fetchall()
        for seq, bytes_gz in rows:
            if total <= self.max_total_bytes:
                break
            p = self._sealed_path(seq)
            if p.exists():
                p.unlink()
            self._db.execute("DELETE FROM chunks WHERE seq=?", (seq,))
            self._db.commit()
            total -= bytes_gz or 0

    # --- paths / counters ----------------------------------------------

    def _allocate_seq(self) -> int:
        cur = 0
        if self._seq_path.exists():
            try:
                cur = int(self._seq_path.read_text(encoding="utf-8").strip())
            except ValueError:
                cur = 0
        cur += 1
        self._seq_path.write_text(str(cur), encoding="utf-8")
        return cur

    def _open_path_for(self, seq: int) -> Path:
        return self.dir / f"open-{seq:010d}.jsonl"

    def _sealed_path(self, seq: int) -> Path:
        return self.dir / f"sealed-{seq:010d}.jsonl.gz"

    @staticmethod
    def _count_lines(path: Path) -> int:
        n = 0
        with open(path, "rb") as fp:
            for _ in fp:
                n += 1
        return n

    @staticmethod
    def _truncate_partial_last_line(path: Path) -> None:
        data = path.read_bytes()
        if not data:
            return
        if data.endswith(b"\n"):
            return
        idx = data.rfind(b"\n")
        if idx == -1:
            path.write_bytes(b"")
            return
        path.write_bytes(data[: idx + 1])
