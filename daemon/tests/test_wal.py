from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

from ogcaibb.tracing.record import Trace
from ogcaibb.tracing.wal import WAL


def _trace(text: str = "hello") -> Trace:
    return Trace(
        workstation_id="ws-0",
        daemon_version="0.0.0-test",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model="qwen-test",
        assistant_text=text,
    )


def test_append_creates_open_chunk(tmp_path):
    wal = WAL(tmp_path, max_bytes=10_000, max_age_seconds=600)
    try:
        wal.append_trace(_trace())
        stats = wal.stats()
        assert stats["open_chunk_seq"] >= 1
        assert stats["open_chunk_records"] == 1
        opens = list(tmp_path.glob("open-*.jsonl"))
        assert len(opens) == 1
    finally:
        wal.close()


def test_size_rotation_seals_chunk(tmp_path):
    # Tiny max_bytes forces a seal after the first record.
    wal = WAL(tmp_path, max_bytes=128, max_age_seconds=600)
    try:
        wal.append_trace(_trace(text="x" * 200))
        sealed = wal.list_sealed()
        assert len(sealed) == 1
        gz = sealed[0].path
        assert gz.exists()
        with gzip.open(gz, "rt") as fp:
            line = fp.readline()
        rec = json.loads(line)
        assert rec["kind"] == "trace"
        assert rec["payload"]["model"] == "qwen-test"
    finally:
        wal.close()


def test_mark_uploaded_removes_chunk(tmp_path):
    wal = WAL(tmp_path, max_bytes=64, max_age_seconds=600)
    try:
        wal.append_trace(_trace(text="x" * 200))
        sealed = wal.list_sealed()
        assert sealed
        wal.mark_uploaded(sealed[0].seq)
        assert not sealed[0].path.exists()
        assert wal.list_sealed() == []
    finally:
        wal.close()


def test_recovers_open_chunk_on_restart(tmp_path):
    wal = WAL(tmp_path, max_bytes=10_000, max_age_seconds=600)
    wal.append_trace(_trace())
    # Simulate crash: don't call close(); just drop the handle.
    del wal

    wal2 = WAL(tmp_path, max_bytes=10_000, max_age_seconds=600)
    try:
        # Recovery sealed the recovered chunk and started fresh.
        sealed = wal2.list_sealed()
        assert len(sealed) == 1
    finally:
        wal2.close()


def test_disk_budget_drops_oldest(tmp_path):
    wal = WAL(
        tmp_path,
        max_bytes=64,
        max_age_seconds=600,
        max_total_bytes=200,  # only ~1 chunk fits
    )
    try:
        for _ in range(5):
            wal.append_trace(_trace(text="x" * 400))
        sealed = wal.list_sealed()
        total = sum(c.bytes_gz for c in sealed)
        assert total <= 200
    finally:
        wal.close()
