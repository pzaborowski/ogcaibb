"""End-to-end retrieval tests.

Covers four layers in isolation, then a roundtrip through the hub:

  1. InMemoryVectorStore: upsert + filtered query + cosine ordering
  2. Hub /v1/ingest: indexes a trace via a fake embedder
  3. Hub /v1/retrieve: query embeds + returns top-k via the same fake embedder
  4. format_exemplars: workstation-side prompt-section composer

Qdrant impl is exercised separately and skipped when qdrant-client isn't
installed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

HUB_SRC = Path(__file__).resolve().parents[2] / "hub" / "src"
sys.path.insert(0, str(HUB_SRC))

from ogcaibb.hub_client import endpoints as hub_endpoints
from ogcaibb.loop import (
    _EXEMPLARS_MARKER,
    _resolve_system_prompt,
    format_exemplars,
)
from ogcaibb_hub.storage.vectors import VectorRow
from ogcaibb_hub.storage.vectors.memory import InMemoryVectorStore


# --- VectorStore (in-memory) -------------------------------------------------


@pytest.mark.asyncio
async def test_memory_vector_store_ranks_by_cosine():
    store = InMemoryVectorStore()
    await store.upsert(
        row=VectorRow(
            trace_id="a",
            workstation_id="ws-1",
            vector=[1.0, 0.0],
            user_message="exact match candidate",
            assistant_text="A",
        )
    )
    await store.upsert(
        row=VectorRow(
            trace_id="b",
            workstation_id="ws-1",
            vector=[0.0, 1.0],
            user_message="orthogonal candidate",
            assistant_text="B",
        )
    )
    hits = await store.query(vector=[1.0, 0.1], top_k=2)
    assert [h.trace_id for h in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score


@pytest.mark.asyncio
async def test_memory_vector_store_applies_filters():
    store = InMemoryVectorStore()
    await store.upsert(row=VectorRow(
        trace_id="a", workstation_id="ws-1", vector=[1.0, 0.0],
        user_message="x", assistant_text="x", agent="alpha",
    ))
    await store.upsert(row=VectorRow(
        trace_id="b", workstation_id="ws-2", vector=[1.0, 0.0],
        user_message="x", assistant_text="x", agent="beta",
    ))
    hits = await store.query(vector=[1.0, 0.0], top_k=5, filter_agent="alpha")
    assert {h.trace_id for h in hits} == {"a"}
    hits = await store.query(vector=[1.0, 0.0], top_k=5, filter_workstation="ws-2")
    assert {h.trace_id for h in hits} == {"b"}


@pytest.mark.asyncio
async def test_memory_vector_store_min_score_filter():
    store = InMemoryVectorStore()
    await store.upsert(row=VectorRow(
        trace_id="a", workstation_id="ws-1", vector=[1.0, 0.0],
        user_message="x", assistant_text="x",
    ))
    hits = await store.query(vector=[0.0, 1.0], top_k=5, min_score=0.5)
    assert hits == []


# --- Hub ingest+retrieve roundtrip with a fake embedder ---------------------


class _FakeEmbedder:
    name = "fake"
    dim = 4

    def __init__(self) -> None:
        self._table: dict[str, list[float]] = {
            # Two semantically similar prompts → same vector; one different.
            "how do I write a STAC item from CSV?": [1.0, 0.0, 0.0, 0.0],
            "writing a STAC item out of a CSV":     [1.0, 0.0, 0.0, 0.0],
            "what is the capital of France?":       [0.0, 0.0, 0.0, 1.0],
        }

    async def embed(self, texts):
        return [self._table.get(t, [0.0, 0.0, 1.0, 0.0]) for t in texts]

    async def aclose(self) -> None:
        return


@pytest.fixture
def hub_app_with_retrieval(tmp_path, monkeypatch):
    keys_file = tmp_path / "keys.yaml"
    token = "retrieve-test-token"
    digest = hashlib.sha256(token.encode()).hexdigest()
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
    from ogcaibb_hub.main import app  # noqa: WPS433
    return app, token, tmp_path


@pytest.mark.asyncio
async def test_ingest_indexes_into_vector_store(hub_app_with_retrieval):
    app, token, _ = hub_app_with_retrieval
    chunk = _build_chunk(
        trace_id="t-stac-1",
        user_msg="how do I write a STAC item from CSV?",
        assistant_text="Use the metadata-extraction skill, then ...",
    )

    async with app.router.lifespan_context(app):
        # Inject our fake embedder + an in-memory vector store.
        app.state.embedder = _FakeEmbedder()
        app.state.vector_store = InMemoryVectorStore()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            r = await client.post(
                f"http://hub.invalid{hub_endpoints.INGEST}",
                content=chunk["body"], headers={**chunk["headers"],
                                                "Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["accepted"] == 1
            assert await app.state.vector_store.count() == 1


@pytest.mark.asyncio
async def test_retrieve_returns_similar_exemplar(hub_app_with_retrieval):
    app, token, _ = hub_app_with_retrieval
    chunk = _build_chunk(
        trace_id="t-stac-2",
        user_msg="how do I write a STAC item from CSV?",
        assistant_text="Use the metadata-extraction skill, then ...",
        seq=2,
    )

    async with app.router.lifespan_context(app):
        app.state.embedder = _FakeEmbedder()
        app.state.vector_store = InMemoryVectorStore()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            await client.post(
                f"http://hub.invalid{hub_endpoints.INGEST}",
                content=chunk["body"], headers={**chunk["headers"],
                                                "Authorization": f"Bearer {token}"},
            )
            r = await client.post(
                f"http://hub.invalid{hub_endpoints.RETRIEVE}",
                json={"query": "writing a STAC item out of a CSV", "top_k": 5},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert len(data["results"]) == 1
            assert data["results"][0]["trace_id"] == "t-stac-2"
            assert data["results"][0]["score"] > 0.99


@pytest.mark.asyncio
async def test_retrieve_503_when_no_embedder(hub_app_with_retrieval):
    app, token, _ = hub_app_with_retrieval
    async with app.router.lifespan_context(app):
        # leave embedder/vector_store as None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            r = await client.post(
                f"http://hub.invalid{hub_endpoints.RETRIEVE}",
                json={"query": "anything"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 503


# --- Workstation-side formatter --------------------------------------------


def test_format_exemplars_renders_section():
    out = format_exemplars(
        [
            {
                "trace_id": "t-1",
                "score": 0.92,
                "user_message": "How do I X?",
                "assistant_text": "By calling Y.",
                "agent": "building-block-generator",
                "skill": None,
                "model": "qwen2.5-coder:32b",
            }
        ]
    )
    assert out is not None
    assert _EXEMPLARS_MARKER in out
    assert "How do I X?" in out
    assert "By calling Y." in out
    assert "agent=building-block-generator" in out
    assert "score=0.92" in out


def test_format_exemplars_returns_none_when_empty():
    assert format_exemplars([]) is None
    # Items with neither user nor assistant text are skipped.
    assert format_exemplars([{"user_message": "", "assistant_text": ""}]) is None


def test_resolver_includes_exemplars_between_menu_and_rules():
    exemplars = format_exemplars(
        [
            {
                "user_message": "Q",
                "assistant_text": "A",
                "agent": "g",
                "skill": "s",
                "score": 0.7,
            }
        ]
    )
    menu = "## Available skills, agents, and slash commands\n(none)"
    resolved = _resolve_system_prompt("BODY", menu=menu, exemplars=exemplars)
    i_body = resolved.index("BODY")
    i_menu = resolved.index("## Available skills")
    i_exemplars = resolved.index(_EXEMPLARS_MARKER)
    i_rules = resolved.index("CRITICAL RULES")
    assert i_body < i_menu < i_exemplars < i_rules


# --- helpers ----------------------------------------------------------------


def _build_chunk(*, trace_id: str, user_msg: str, assistant_text: str, seq: int = 1):
    payload = {
        "kind": "trace",
        "payload": {
            "schema_version": 1,
            "trace_id": trace_id,
            "workstation_id": "ws-test",
            "daemon_version": "test",
            "started_at": "2026-05-17T10:00:00+00:00",
            "ended_at": "2026-05-17T10:00:01+00:00",
            "model": "qwen-test",
            "agent": None,
            "skill": None,
            "messages_in": [{"role": "user", "content": user_msg}],
            "tool_calls": [],
            "assistant_text": assistant_text,
        },
    }
    body_text = json.dumps(payload) + "\n"
    body_gz = gzip.compress(body_text.encode("utf-8"))
    return {
        "body": body_gz,
        "headers": {
            "Content-Type": "application/x-ogcaibb-trace-chunk",
            "Content-Encoding": "gzip",
            "X-Schema-Version": "1",
            "X-Workstation-Id": "ws-test",
            "X-Chunk-Sequence": str(seq),
            "X-Chunk-Sha256": hashlib.sha256(body_gz).hexdigest(),
            "X-Chunk-Records": "1",
        },
    }
