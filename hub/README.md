# ogcaibb-hub

Ingestion + retrieval service for trace chunks produced by ogcaibb daemons.
Designed to live on the **proxy box** (next to Caddy/Traefik), not on the GPU
box hosting Ollama. Embeddings/retrieval are added in a follow-up release;
v0.1 covers auth + ingest + raw storage + SQLite index.

## Layout

```
hub/
├── pyproject.toml
└── src/ogcaibb_hub/
    ├── main.py            FastAPI app
    ├── cli.py             ogcaibb-hub CLI (serve, issue-key, list-keys, count)
    ├── config.py          env-driven settings
    ├── auth/              pluggable HubAuth (apikey today; github later)
    ├── storage/           pluggable TraceStore + IndexStore
    └── api/               /v1/ingest, /healthz
```

Every concrete impl sits behind a Protocol in `*/base.py` and is selected by
config (`OGCAIBB_HUB_AUTH`, `OGCAIBB_HUB_TRACE_STORE`, `OGCAIBB_HUB_INDEX_STORE`).
Swapping a backend is one config flip + a new file in the relevant registry.

## Install + run

```bash
cd hub
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env
# edit OGCAIBB_HUB_APIKEYS_FILE etc.

ogcaibb-hub serve
```

## Issuing keys (manual, server-side)

```bash
ogcaibb-hub issue-key --name piotr-laptop
# prints:
#   Token (give to developer): ogcaibb_pat_…
#   Append to /etc/ogcaibb-hub/keys.yaml:
#     keys:
#       - name: piotr-laptop
#         sub: apikey:piotr-laptop
#         key_sha256: …
#         revoked: false
```

Paste the YAML snippet into `keys.yaml`. The hub reloads the file on its own
(mtime-based; checks every few seconds). Revoke by flipping `revoked: true`.

## Ingest contract

```
POST /v1/ingest
Authorization: Bearer <token>
Content-Type: application/x-ogcaibb-trace-chunk
Content-Encoding: gzip
X-Schema-Version: 1
X-Workstation-Id: <uuid>
X-Chunk-Sequence: <int>
X-Chunk-Sha256: <hex>
X-Chunk-Records: <int>

<gzipped JSONL>
  {"kind": "trace",  "payload": { … }}
  {"kind": "signal", "payload": { … }}
```

Response:

```json
{"accepted": 12, "duplicates": 0, "errors": []}
```

The workstation uploader retries on 5xx and parks chunks on 401/403/415.

## Caddy route

```caddy
hub.example.org {
    @authorized header Authorization "Bearer *"
    handle @authorized {
        reverse_proxy localhost:8080
    }
    respond 401
}
```

Caddy enforces presence of a Bearer header; the hub enforces *which* token.
