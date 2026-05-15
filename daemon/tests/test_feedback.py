"""Tests for explicit feedback: workstation /v1/feedback writes a Signal to
the WAL; subsequent upload through the hub roundtrip persists into the
signals table.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# Workstation app needs the daemon already imported above; the hub package
# lives in a sibling tree and is needed for the roundtrip test below.
HUB_SRC = Path(__file__).resolve().parents[2] / "hub" / "src"
sys.path.insert(0, str(HUB_SRC))

from ogcaibb.hub_client import endpoints as hub_endpoints  # noqa: E402


@pytest.fixture
def workstation_app(tmp_path, monkeypatch):
    """Build a daemon app rooted at tmp_path with trace capture enabled but
    no network transport — feedback writes stay local."""
    monkeypatch.setenv("OGCAIBB_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("OGCAIBB_WORKSTATION_ID_PATH", str(tmp_path / "id"))
    monkeypatch.setenv("OGCAIBB_TRACE_TRANSPORT", "noop")
    monkeypatch.setenv("OGCAIBB_TRACE_ENABLED", "1")

    for mod in list(sys.modules):
        if mod.startswith("ogcaibb.") or mod == "ogcaibb":
            del sys.modules[mod]
    from ogcaibb.main import app
    return app, tmp_path


@pytest.mark.asyncio
async def test_feedback_writes_signal_to_wal(tmp_path, workstation_app):
    app, root = workstation_app
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                f"http://daemon.invalid{hub_endpoints.FEEDBACK}",
                json={"trace_id": "t-abc", "rating": "up", "comment": "looked good"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["polarity"] == 1

            resp_down = await client.post(
                f"http://daemon.invalid{hub_endpoints.FEEDBACK}",
                json={"trace_id": "t-abc", "rating": "down"},
            )
            assert resp_down.status_code == 200
            assert resp_down.json()["polarity"] == -1

    # Force-seal so the open chunk becomes inspectable.
    from ogcaibb.tracing.wal import WAL
    wal = WAL(root / "traces", max_bytes=999_999, max_age_seconds=999_999)
    try:
        wal.seal_now()
        sealed = wal.list_sealed()
        assert sealed, "expected the feedback writes to land in a sealed chunk"
        with gzip.open(sealed[0].path, "rt") as fp:
            records = [json.loads(line) for line in fp if line.strip()]
    finally:
        wal.close()

    signals = [r for r in records if r["kind"] == "signal"]
    assert len(signals) == 2
    assert {s["payload"]["polarity"] for s in signals} == {1, -1}
    assert signals[0]["payload"]["trace_id"] == "t-abc"
    assert signals[0]["payload"]["meta"]["comment"] == "looked good"


@pytest.mark.asyncio
async def test_feedback_rejects_unknown_rating(workstation_app):
    app, _ = workstation_app
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                f"http://daemon.invalid{hub_endpoints.FEEDBACK}",
                json={"trace_id": "t-x", "rating": "love-it"},
            )
            assert resp.status_code == 400


@pytest.mark.asyncio
async def test_signal_roundtrips_to_hub(tmp_path, monkeypatch):
    """Build a hub, push a chunk containing one signal envelope, assert the
    SQLite index has a row in the signals table afterward.
    """
    keys_file = tmp_path / "keys.yaml"
    token = "feedback-roundtrip-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    keys_file.write_text(
        f"keys:\n  - name: tester\n    sub: apikey:tester\n    key_sha256: {digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OGCAIBB_HUB_APIKEYS_FILE", str(keys_file))
    monkeypatch.setenv("OGCAIBB_HUB_TRACE_STORE_PATH", str(tmp_path / "chunks"))
    monkeypatch.setenv("OGCAIBB_HUB_INDEX_STORE_PATH", str(tmp_path / "index.db"))
    for mod in list(sys.modules):
        if mod.startswith("ogcaibb_hub"):
            del sys.modules[mod]
    from ogcaibb_hub.main import app
    from ogcaibb_hub.storage.registry import get_index_store

    # Build a chunk with one signal envelope.
    detected = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    body_text = json.dumps(
        {
            "kind": "signal",
            "payload": {
                "trace_id": "trace-roundtrip-1",
                "workstation_id": "ws-feedback",
                "source": "explicit",
                "polarity": 1,
                "weight": 1.0,
                "detected_at": detected.isoformat(),
                "meta": {"comment": "nice"},
            },
        }
    ) + "\n"
    body_gz = gzip.compress(body_text.encode("utf-8"))
    sha = hashlib.sha256(body_gz).hexdigest()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                f"http://hub.invalid{hub_endpoints.INGEST}",
                content=body_gz,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-ogcaibb-trace-chunk",
                    "Content-Encoding": "gzip",
                    "X-Schema-Version": "1",
                    "X-Workstation-Id": "ws-feedback",
                    "X-Chunk-Sequence": "42",
                    "X-Chunk-Sha256": sha,
                    "X-Chunk-Records": "1",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accepted"] == 1

            # Second send is a duplicate at the signal layer.
            resp2 = await client.post(
                f"http://hub.invalid{hub_endpoints.INGEST}",
                content=body_gz,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-ogcaibb-trace-chunk",
                    "Content-Encoding": "gzip",
                    "X-Schema-Version": "1",
                    "X-Workstation-Id": "ws-feedback",
                    "X-Chunk-Sequence": "42",
                    "X-Chunk-Sha256": sha,
                    "X-Chunk-Records": "1",
                },
            )
            assert resp2.status_code == 200
            assert resp2.json()["duplicates"] == 1

    idx = get_index_store("sqlite", db_path=tmp_path / "index.db")
    try:
        assert await idx.count_signals(trace_id="trace-roundtrip-1") == 1
    finally:
        await idx.close()
