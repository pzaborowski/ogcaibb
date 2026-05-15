"""IndexStore impl backed by SQLite.

Schema is intentionally narrow: just what's needed to dedupe traces, count
traffic per workstation, and (later) join ratings. Big payloads stay in the
raw TraceStore.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .base import SignalRow, TraceRow

log = logging.getLogger(__name__)


class SQLiteIndexStore:
    name = "sqlite"

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path.expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                workstation_id TEXT NOT NULL,
                received_at REAL NOT NULL,
                model TEXT,
                agent TEXT,
                skill TEXT,
                bytes_compressed INTEGER
            );
            CREATE INDEX IF NOT EXISTS traces_ws_received
                ON traces(workstation_id, received_at);

            CREATE TABLE IF NOT EXISTS signals (
                trace_id TEXT NOT NULL,
                workstation_id TEXT NOT NULL,
                source TEXT NOT NULL,
                polarity INTEGER NOT NULL,
                weight REAL NOT NULL,
                detected_at REAL NOT NULL,
                comment TEXT,
                PRIMARY KEY (trace_id, source, detected_at)
            );
            CREATE INDEX IF NOT EXISTS signals_by_trace ON signals(trace_id);
            """
        )
        self._db.commit()

    async def upsert_trace(self, *, row: TraceRow) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO traces"
            "(trace_id, workstation_id, received_at, model, agent, skill, bytes_compressed)"
            "VALUES(?,?,?,?,?,?,?)",
            (
                row.trace_id,
                row.workstation_id,
                row.received_at,
                row.model,
                row.agent,
                row.skill,
                row.bytes_compressed,
            ),
        )
        self._db.commit()
        return cur.rowcount > 0

    async def upsert_signal(self, *, row: SignalRow) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO signals"
            "(trace_id, workstation_id, source, polarity, weight, detected_at, comment)"
            "VALUES(?,?,?,?,?,?,?)",
            (
                row.trace_id,
                row.workstation_id,
                row.source,
                row.polarity,
                row.weight,
                row.detected_at,
                row.comment,
            ),
        )
        self._db.commit()
        return cur.rowcount > 0

    async def count_signals(self, *, trace_id: str | None = None) -> int:
        if trace_id is None:
            row = self._db.execute("SELECT COUNT(*) FROM signals").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM signals WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
        return int(row[0])

    async def count_traces(self, *, workstation_id: str | None = None) -> int:
        if workstation_id is None:
            row = self._db.execute("SELECT COUNT(*) FROM traces").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM traces WHERE workstation_id=?",
                (workstation_id,),
            ).fetchone()
        return int(row[0])

    async def close(self) -> None:
        self._db.close()
