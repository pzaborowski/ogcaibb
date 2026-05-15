"""End-to-end smoke: workstation WAL → HTTPSTransport → hub /v1/ingest.

Runs the hub FastAPI app in-process via httpx.ASGITransport, so no network
ports are opened. Exercises the auth middleware, header validation, gzip
sha256 verification, raw chunk persistence, and SQLite index dedupe.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# Make the hub package importable from this daemon test process.
HUB_SRC = Path(__file__).resolve().parents[2] / "hub" / "src"
sys.path.insert(0, str(HUB_SRC))

from ogcaibb.hub_client import endpoints as hub_endpoints
from ogcaibb.hub_client.auth.apikey import APIKeyAuth
from ogcaibb.tracing.record import Trace
from ogcaibb.tracing.transport.base import ChunkManifest
from ogcaibb.tracing.transport.https import HTTPSTransport
from ogcaibb.tracing.wal import WAL


@pytest.fixture
def hub_app(tmp_path, monkeypatch):
    keys_file = tmp_path / "keys.yaml"
    token = "test-token-abc123"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    keys_file.write_text(
        "keys:\n"
        f"  - name: tester\n"
        f"    sub: apikey:tester\n"
        f"    key_sha256: {digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OGCAIBB_HUB_APIKEYS_FILE", str(keys_file))
    monkeypatch.setenv("OGCAIBB_HUB_TRACE_STORE_PATH", str(tmp_path / "chunks"))
    monkeypatch.setenv("OGCAIBB_HUB_INDEX_STORE_PATH", str(tmp_path / "index.db"))

    # Force-reload modules so they pick up the patched env.
    for mod in list(sys.modules):
        if mod.startswith("ogcaibb_hub"):
            del sys.modules[mod]
    from ogcaibb_hub.main import app

    return app, token, tmp_path


@pytest.mark.asyncio
async def test_workstation_to_hub_roundtrip(tmp_path, hub_app):
    app, token, hub_root = hub_app

    # Write one trace, force-seal.
    wal = WAL(tmp_path / "wal", max_bytes=64, max_age_seconds=600)
    try:
        t = Trace(
            workstation_id="ws-roundtrip",
            daemon_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model="qwen-test",
            assistant_text="hello world",
        )
        wal.append_trace(t)
        wal.seal_now()
        sealed = wal.list_sealed()
        assert len(sealed) == 1
        chunk = sealed[0]
    finally:
        wal.close()

    async with app.router.lifespan_context(app):
        auth = APIKeyAuth(token)
        transport = HTTPSTransport.__new__(HTTPSTransport)
        transport._hub_url = "http://hub.invalid"
        transport._auth = auth
        transport._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

        manifest = ChunkManifest(
            workstation_id="ws-roundtrip",
            sequence=chunk.seq,
            sha256=chunk.sha256,
            bytes_gz=chunk.bytes_gz,
            record_count=chunk.record_count,
            schema_version=1,
        )
        try:
            ack = await transport.send(chunk.path, manifest)
        finally:
            await transport.aclose()

    assert ack.accepted == 1
    assert ack.duplicates == 0
    assert ack.errors == []

    chunks_root = hub_root / "chunks"
    assert any(chunks_root.rglob("*.jsonl.gz"))


@pytest.mark.asyncio
async def test_auth_rejected_without_token(tmp_path, hub_app):
    app, _token, _ = hub_app
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                f"http://hub.invalid{hub_endpoints.INGEST}",
                content=b"",
                headers={
                    "X-Schema-Version": "1",
                    "X-Workstation-Id": "ws",
                    "X-Chunk-Sequence": "1",
                    "X-Chunk-Sha256": "0" * 64,
                },
            )
    assert resp.status_code == 401
