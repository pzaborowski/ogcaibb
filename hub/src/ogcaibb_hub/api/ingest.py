"""POST /v1/ingest — accept a gzipped JSONL chunk of traces.

Headers from the workstation:
    Authorization: Bearer <token>
    Content-Type: application/x-ogcaibb-trace-chunk
    Content-Encoding: gzip
    X-Schema-Version: 1
    X-Workstation-Id: <uuid>
    X-Chunk-Sequence: <int>
    X-Chunk-Sha256: <hex>
    X-Chunk-Records: <int>

Body: raw gzipped JSONL bytes; one JSON object per line, each of shape
    {"kind": "trace"|"signal", "payload": {...}}
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from ..auth.base import Identity
from ..config import settings
from ..storage.base import IngestSummary, TraceRow

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/ingest")
async def ingest(
    request: Request,
    x_schema_version: int = Header(...),
    x_workstation_id: str = Header(...),
    x_chunk_sequence: int = Header(...),
    x_chunk_sha256: str = Header(...),
    x_chunk_records: int = Header(0),
) -> dict[str, Any]:
    identity: Identity = request.state.identity

    if x_schema_version not in settings.accepted_schema_versions:
        raise HTTPException(415, f"unsupported schema_version={x_schema_version}")

    body = await request.body()
    if len(body) > settings.max_chunk_bytes:
        raise HTTPException(413, f"chunk exceeds {settings.max_chunk_bytes} bytes")

    actual_sha = hashlib.sha256(body).hexdigest()
    if actual_sha.lower() != x_chunk_sha256.lower().strip():
        raise HTTPException(400, "X-Chunk-Sha256 mismatch")

    try:
        decompressed = gzip.decompress(body)
    except OSError as e:
        raise HTTPException(400, f"gzip decode failed: {e}") from e

    summary = IngestSummary()
    received_at = time.time()
    trace_store = request.app.state.trace_store
    index_store = request.app.state.index_store

    # Persist the raw chunk first — cheap, fits the "store-then-index" pattern
    # and means we can re-index later without re-uploading.
    await trace_store.put_chunk(
        workstation_id=x_workstation_id,
        sequence=x_chunk_sequence,
        sha256=actual_sha,
        body_gz=body,
    )

    for lineno, line in enumerate(decompressed.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as e:
            summary.errors.append(f"line {lineno}: bad JSON: {e}")
            continue
        kind = envelope.get("kind")
        payload = envelope.get("payload") or {}
        if kind == "trace":
            try:
                row = TraceRow(
                    trace_id=str(payload["trace_id"]),
                    workstation_id=str(payload.get("workstation_id", x_workstation_id)),
                    received_at=received_at,
                    model=str(payload.get("model", "")),
                    agent=payload.get("agent"),
                    skill=payload.get("skill"),
                    bytes_compressed=len(body) // max(x_chunk_records or 1, 1),
                )
            except KeyError as e:
                summary.errors.append(f"line {lineno}: missing field {e}")
                continue
            new = await index_store.upsert_trace(row=row)
            if new:
                summary.accepted += 1
            else:
                summary.duplicates += 1
        elif kind == "signal":
            # v0.1: stored but not indexed beyond what /feedback will do next.
            summary.accepted += 1
        else:
            summary.errors.append(f"line {lineno}: unknown kind={kind!r}")

    log.info(
        "ingest from sub=%s ws=%s seq=%d records=%d accepted=%d dup=%d errors=%d",
        identity.sub, x_workstation_id, x_chunk_sequence, x_chunk_records,
        summary.accepted, summary.duplicates, len(summary.errors),
    )

    return {
        "accepted": summary.accepted,
        "duplicates": summary.duplicates,
        "errors": summary.errors,
    }
