# Architecture

Status as of 2026-05-17. Supersedes the v0.1.0 snapshot. Tracks the feedback
loop substrate (trace capture, hub ingestion, vector retrieval) on top of the
original local-Ollama-only daemon.

## Deployment topology

Three roles, two on-prem machines plus N developer workstations:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Machine 1 — GPU box                                                 │
│   Ollama  ──  qwen2.5-coder:32b · nomic-embed-text · …               │
│   bound to private VLAN only; no public ports                        │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ private HTTP, no TLS, no auth
┌──────────────────────────────┴───────────────────────────────────────┐
│  Machine 2 — proxy + hub                                             │
│                                                                      │
│   Caddy (TLS termination, bearer auth)                               │
│     ├─ ollama.example.org    → gpu-host.internal:11434               │
│     └─ hub.example.org       → localhost:8080                        │
│                                                                      │
│   ogcaibb-hub  (systemd, FastAPI, :8080)                             │
│     · POST /v1/ingest    raw trace + signal chunks                   │
│     · POST /v1/feedback  (future, currently via workstation)         │
│     · POST /v1/retrieve  top-k exemplars by similarity               │
│     · GET  /healthz                                                  │
│     embedder → http://gpu-host.internal:11434/api/embed              │
│     vectors  → Qdrant on localhost:6333 (or in-memory for dev)       │
│     state    → /var/lib/ogcaibb-hub/{chunks/,state.db,qdrant/}       │
└─────────────────────────────────▲────────────────────────────────────┘
                                  │ HTTPS + Bearer (per-developer token)
┌─────────────────────────────────┴─────────────────────────────────┐
│  Developer workstation                                            │
│                                                                   │
│   IDE / CLI                                                       │
│     Continue.dev (VS Code/JetBrains/Zed) · aider · ogcaibb CLI    │
│                              │ OpenAI-compat /v1/chat/completions │
│                              ▼                                    │
│   ogcaibb daemon (FastAPI on 127.0.0.1:4141)                      │
│     ┌─────────────────────────────────────────────────────────┐   │
│     │ commands.parser   → expands /slash → skill + agent      │   │
│     │ skills.registry   → SKILL.md frontmatter + body         │   │
│     │ agents.registry   → agent manifest + tool allowlist     │   │
│     │ loop.run_turn     → PydanticAI Agent over Ollama        │   │
│     │ retrieval         → calls hub /v1/retrieve (optional)   │   │
│     │ tracing pipeline  → WAL → uploader → hub /v1/ingest     │   │
│     │ signals.observer  → implicit detectors                  │   │
│     └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│   Local state:                                                    │
│     ~/.local/share/ogcaibb/traces/{open-*,sealed-*.gz,manifest.db}│
│     ~/.local/share/ogcaibb/{memory/, chroma/, workstation_id}     │
└───────────────────────────────────────────────────────────────────┘
```

Failure-isolation note: if the hub or the GPU box is unreachable, chat still
works (hub failure) or fails loudly with HTTP 503 (GPU box failure — hard rule,
no fallback). Traces buffer on disk up to 200 MB while the hub is down.

## Components

### Workstation daemon  (`daemon/src/ogcaibb/`)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app exposing OpenAI-compat `/v1/chat/completions`, `/v1/models`, `/v1/feedback`, `/healthz`. Owns the full request lifecycle below. |
| `loop.py` | `run_turn` / `run_turn_stream` build a PydanticAI `Agent` per turn against Ollama via OpenAI-compat. Owns system-prompt composition (`_resolve_system_prompt`) and the menu/exemplar formatters. |
| `cli.py` | Operator commands: `serve`, `ping`, `skills`, `agents`, `commands`, `feedback <trace_id>`, `traces {status,tail}`. |
| `config.py` | All env-driven settings (Pydantic). |
| `ollama_client.py` | Thin httpx client to the configured Ollama. Used for health checks; the agent loop talks to Ollama via PydanticAI's OpenAI provider. |
| `skills/registry.py` | Loads `SKILL.md` files (frontmatter + body) from `manifests_root/skills`. |
| `agents/registry.py` | Loads agent manifests from `manifests_root/agents`. |
| `commands/parser.py` | Parses leading `/<name>` in user messages, substitutes `$ARGUMENTS`, attaches `skill:`/`agent:` from command frontmatter. |
| `tools/` | In-process tools: `Read`, `Write`, `Edit`, `Bash`, `WebFetch`, `BblocksPostprocess`, `Pygeoapi{Up,Down}`, `ValidateAgainstSchema`, `CheckContextCompleteness`, `EmbedUpsert`, `EmbedQuery`. Filtered per agent allowlist. |
| `tracing/record.py` | `Trace`, `Signal`, `ToolCallRecord` Pydantic models; `load_workstation_id` (stable UUID per install). |
| `tracing/redactor/` | `Redactor` Protocol + `RuleSetRedactor` (secrets regex, path denylist, length cap, configurable via YAML). |
| `tracing/wal.py` | Append-only JSONL WAL with size/time rotation, gzip seal, SQLite manifest, 200 MiB local budget with oldest-drop. Crash-recovers partial last lines. |
| `tracing/transport/` | `TraceTransport` Protocol + `NoopTransport` (default, offline) and `HTTPSTransport` (gzip body + `X-Chunk-Sha256` headers). |
| `tracing/uploader.py` | Async background task that drains sealed chunks via the configured transport. Exponential backoff with jitter; non-retriable errors park the chunk. |
| `tracing/signals/` | `ImplicitSignal` Protocol + `SignalContext`; three concrete detectors (`tool_call_completed_clean`, `edit_retention`, `git_commit`); `SignalObserver` schedules them per turn and persists emitted `Signal`s. |
| `hub_client/` | `HubClient.{health,retrieve,aclose}`; `WorkstationAuth` Protocol + `APIKeyAuth` (bearer); shared `endpoints.{HEALTH,INGEST,FEEDBACK,RETRIEVE}` constants. |

### Hub  (`hub/src/ogcaibb_hub/`)

Separate Python package, deployed on the proxy box.

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app + auth middleware. Lifespan wires the configured auth, trace store, index store, embedder, vector store. |
| `cli.py` | Operator commands: `serve`, `issue-key`, `list-keys`, `count`. |
| `config.py` | Pydantic settings; all storage paths and backend selectors. |
| `endpoints.py` | Path constants mirroring the workstation's `hub_client/endpoints.py`. |
| `auth/` | `HubAuth` Protocol + `APIKeyAuth` (sha256-of-token match against `keys.yaml`, mtime-watched reload). `Identity{sub, kind, display_name, attrs}` attached to every request. |
| `storage/raw/` | `TraceStore` Protocol + `LocalFSTraceStore` (gz chunks at `<root>/<workstation_id>/<seq>.jsonl.gz`, atomic write). |
| `storage/index/` | `IndexStore` Protocol + `SQLiteIndexStore` (traces + signals tables, dedupe on `trace_id`). |
| `storage/vectors/` | `VectorStore` Protocol + `InMemoryVectorStore` (cosine, dependency-free) and `QdrantVectorStore` (lazy-imports `qdrant-client`, auto-creates the collection at the embedder's `dim`). |
| `embedder/` | `Embedder` Protocol + `OllamaEmbedder` (probes `dim` on first call). |
| `api/ingest.py` | `POST /v1/ingest`: bearer-authenticate, sha256-verify, gunzip, persist raw chunk, dedupe traces in SQLite, persist signals, synchronously embed + upsert each new trace into the vector store. |
| `api/retrieve.py` | `POST /v1/retrieve`: embed query via Ollama, query vector store with `{agent, skill, scope}` filters, return top-k exemplars. |
| `api/health.py` | `GET /healthz` (unauthenticated). |

## Modularity — plugin seams

Every concrete impl sits behind a `typing.Protocol` and is selected by config.
Switching backends is one config flip + one new file + one `register(...)`
line; no callers change.

| Seam | Protocol | Today's impls | Selector |
|---|---|---|---|
| Workstation auth | `WorkstationAuth` | `APIKeyAuth` | `OGCAIBB_HUB_AUTH=apikey` |
| Trace transport | `TraceTransport` | `NoopTransport`, `HTTPSTransport` | `OGCAIBB_TRACE_TRANSPORT=noop\|https` |
| Redactor | `Redactor` | `RuleSetRedactor` | `OGCAIBB_REDACTOR=ruleset` |
| Implicit signal | `ImplicitSignal` | `tool_call_completed_clean`, `edit_retention`, `git_commit` | `OGCAIBB_IMPLICIT_SIGNALS=...` |
| Hub auth | `HubAuth` | `APIKeyAuth` | `OGCAIBB_HUB_AUTH=apikey` |
| Hub raw store | `TraceStore` | `LocalFSTraceStore` | `OGCAIBB_HUB_TRACE_STORE=localfs` |
| Hub index store | `IndexStore` | `SQLiteIndexStore` | `OGCAIBB_HUB_INDEX_STORE=sqlite` |
| Hub vector store | `VectorStore` | `InMemoryVectorStore`, `QdrantVectorStore` | `OGCAIBB_HUB_VECTOR_STORE=memory\|qdrant` |
| Hub embedder | `Embedder` | `OllamaEmbedder` | `OGCAIBB_HUB_EMBEDDER=ollama` |

## Request lifecycle  (POST /v1/chat/completions)

```
client                              daemon                                       hub                    Ollama
  │  /v1/chat/completions             │                                            │                      │
  │ ────────────────────────────────► │                                            │                      │
  │                                   │  health-check Ollama (HTTP 503 on fail)    │                      │
  │                                   │ ─────────────────────────────────────────────────────────────► │
  │                                   │                                            │                      │
  │                                   │  parse_slash_command → skill/agent         │                      │
  │                                   │                                            │                      │
  │                                   │  _build_menu(skills, agents, commands)     │                      │
  │                                   │                                            │                      │
  │                                   │  _maybe_retrieve_exemplars(latest user msg)│                      │
  │                                   │ ─────────────────────────────────────────► │                      │
  │                                   │   POST /v1/retrieve                        │  embed via /api/embed│
  │                                   │                                            │ ───────────────────► │
  │                                   │ ◄───────────────────────────────────────── │  top-k exemplars     │
  │                                   │                                            │                      │
  │                                   │  _resolve_system_prompt(                   │                      │
  │                                   │    custom=agent/skill body,                │                      │
  │                                   │    menu=…, exemplars=…)                    │                      │
  │                                   │  → body + menu + exemplars + CRITICAL RULES│                      │
  │                                   │                                            │                      │
  │                                   │  PydanticAI Agent.run / iter (tool loop)   │                      │
  │                                   │ ─────────────────────────────────────────────────────────────► │
  │ ◄═════════════════════════════════ │ tokens / tool_calls / reasoning            │ ◄═══════════════════ │
  │   SSE stream (or final JSON)      │                                            │                      │
  │                                   │  build Trace(messages_in, tool_calls, …)   │                      │
  │                                   │  redactor.redact(trace)                    │                      │
  │                                   │  wal.append_trace(trace)                   │                      │
  │                                   │  signal_observer.schedule(trace) ─┐        │                      │
  │                                   │                                   │        │                      │
  │                                   │   …in the background, per detector delay:   │                      │
  │                                   │   detect(ctx) → wal.append_signal(sig)      │                      │
  │                                   │                                             │                      │
  │                                   │  uploader (separate task): sealed chunk →   │                      │
  │                                   │   gzip → POST /v1/ingest → ack → delete    │                      │
  │                                   │ ─────────────────────────────────────────► │                      │
  │                                   │                                            │  embed user msg      │
  │                                   │                                            │ ───────────────────► │
  │                                   │                                            │  upsert to vector    │
```

The chat response itself is returned to the client before any trace persistence
or detector scheduling happens. The hub uploader and signal detectors run on
the daemon's event loop but do not block the response.

## System-prompt composition

Final order, every turn, enforced by `_resolve_system_prompt`:

```
┌────────────────────────────────────────────────────────────┐
│ 1. Caller-supplied prompt                                  │
│    (default intro, or agent body + activated skill body)   │
├────────────────────────────────────────────────────────────┤
│ 2. ## Available skills, agents, and slash commands         │
│    Menu of the local registries with one-line descriptions │
│    so clients that don't speak the skill-extension protocol│
│    (Continue.dev, aider) still see what's on offer.        │
│    Off-switch: OGCAIBB_INJECT_MENU=0                       │
├────────────────────────────────────────────────────────────┤
│ 3. ## Relevant past exemplars (from the team's accepted    │
│    prompts)                                                │
│    Top-k hub-retrieved exemplars for the current user      │
│    message, filtered by active agent/skill and scope.      │
│    Off-switch: OGCAIBB_RETRIEVE_ENABLED=0                  │
├────────────────────────────────────────────────────────────┤
│ 4. CRITICAL RULES (always last — most chat models weight   │
│    most-recent system content highest)                     │
│    Workspace boundary, Write-tool mandate, pre-final file  │
│    checklist, no-vocab-invention, error handling.          │
│    Inherited by every agent — agents cannot drop them.     │
└────────────────────────────────────────────────────────────┘
```

`_resolve_system_prompt` is idempotent on each marker — if a caller embedded
one of them inline, it's not re-appended.

## Trace pipeline

A `Trace` is one complete `/v1/chat/completions` turn including the input
messages, every tool call + result, the final assistant text, reasoning, and
timings. The `trace_id` is generated up-front by the daemon, surfaced to
clients in both response shapes (`ogcaibb.trace_id` JSON; `ogcaibb_trace_id`
per streaming chunk), and persisted alongside the on-disk record.

```
                                       redactor
                                          │
  agent turn completes ──► Trace ─────────▼────────► WAL append
                                                       │
                                                       ▼
                       rotation on size (4MiB) / age (5min)
                                                       │
                                                       ▼
                                            seal → gzip + sha256
                                                       │
                                                       ▼
                                            background uploader
                                                       │
                                  HTTPS + Bearer       ▼
                                ───────────────────────────────── hub /v1/ingest
                                                                       │
                                                                       ▼
                                                       persist raw chunk
                                                       dedupe trace in SQLite
                                                       embed last user message
                                                       upsert vector with metadata
```

WAL file shape:

```
~/.local/share/ogcaibb/traces/
├── manifest.db                        # SQLite: chunk state, sha256, counts
├── .next-seq                          # monotonic counter
├── open-NNNNNNNNNN.jsonl              # currently-appending chunk (at most one)
└── sealed-NNNNNNNNNN.jsonl.gz         # zero or more, awaiting upload
```

Local budget defaults to 200 MiB; once exceeded, the oldest sealed chunk is
dropped with a WARN. Crash recovery on startup: any `open-*.jsonl` from a
previous run is sealed eagerly (after truncating any partial last line).

## Feedback signals

Two sources, one storage path.

### Explicit ratings

```
client → POST /v1/feedback {trace_id, rating: up|down|neutral, comment?}
  → Signal{source="explicit", polarity, weight=1.0}
  → WAL.append_signal → uploader → hub /v1/ingest → SQLiteIndexStore.signals
```

CLI surface: `ogcaibb feedback <trace_id> --rating up|down|neutral`.

### Implicit detectors

`SignalObserver` schedules each enabled detector after every captured turn.
Each detector waits `detector.delay_seconds`, runs `detect(ctx)`, and emits
zero or more `Signal`s into the WAL.

| Detector | Delay | Weight | Polarity rule |
|---|---|---|---|
| `tool_call_completed_clean` | 0s | 0.1 | +1 if no tool returned an error and turn isn't incomplete |
| `edit_retention` | 600s | 0.4 | +1 / 0 / -1 by whether assistant's writes still appear in the files |
| `git_commit` | 1800s | 0.7 | +1 if a commit in `git log --since=<ended_at>` touched any of the written files |

Defaults to only `tool_call_completed_clean` enabled; IO-heavy detectors are
opt-in via `OGCAIBB_IMPLICIT_SIGNALS=...`.

Detectors run on the workstation (need full unredacted access to file
contents, git state). They emit only the numeric `Signal` envelope; raw diffs
and file contents never leave the workstation through the signal path.

## Retrieval loop

The closing-the-loop step: traces accumulate → get indexed → enhance the next
turn's prompt.

```
  daemon  →  POST /v1/retrieve {query, top_k, min_score, filter:{agent, skill, scope}}
  hub    embedder.embed([query]) → vector
         vector_store.query(vector, top_k, filters) → list[RetrievedExemplar]
       ← {"results": [{trace_id, score, user_message, assistant_text, agent, skill, model}, …]}

  daemon  →  format_exemplars(results) → markdown section
         →  _resolve_system_prompt(custom, menu=…, exemplars=…)
         →  PydanticAI Agent.run
```

Filters:

- `agent` / `skill` filter to traces created under the same agent/skill, so
  retrieval is task-aware.
- `scope=self` filters to the caller's own past traces (using identity attrs
  or `sub`); `scope=all` returns anything indexed.

Best-effort semantics: any error (transport, hub 5xx, embedder failure)
returns no exemplars and the turn proceeds as if retrieval were disabled.
Default `OGCAIBB_RETRIEVE_TIMEOUT=2.0` caps the latency hit.

## Auth + identity

One Bearer token per developer, scoped at the proxy:

```
~/.env or shell: OGCAIBB_HUB_TOKEN=ogcaibb_pat_…
                       │
                       ▼ Authorization: Bearer …
              hub.example.org (Caddy)
                       │
                       ▼
              ogcaibb-hub APIKeyAuth
              sha256(token) → keys.yaml lookup
                       │
                       ▼
              Identity{sub, kind, display_name, attrs}
                       │
                       ▼ request.state.identity
              ingest / retrieve / feedback handlers
```

`keys.yaml` is a single file the hub mtime-watches; rotation = `ogcaibb-hub
issue-key --name … --sub …` prints a YAML snippet to paste in and a token to
hand to the developer. The token itself is never stored on the hub.

GitHub OAuth is a planned second provider (slots beside `APIKeyAuth` behind
the same `HubAuth` Protocol).

## Security boundaries

| Boundary | Mechanism |
|---|---|
| Workstation ↔ hub | Bearer over TLS. Caddy enforces presence; hub enforces validity. |
| Workstation ↔ Ollama | Bearer over TLS via the same Caddy + a second vhost. |
| Hub ↔ Ollama (private) | Plain HTTP over the VPC's private VLAN. No public exposure. |
| Workstation file IO | Every `Read`/`Write`/`Edit`/`Bash` resolves paths against `OGCAIBB_WORKSPACE_ROOT` and refuses anything outside. |
| Workstation shell | `Bash` runs under `sandbox-exec` (macOS) / `firejail` (Linux) when available; warns and runs unsandboxed otherwise. |
| Trace privacy | Redactor strips secret patterns + path-denylisted content **before** the WAL write; raw form never touches disk or network. Implicit detectors emit only Signal envelopes, never raw diffs. `.ogcaibbignore` (future) for per-path opt-out. |
| Hub trust model | Per-developer identity tag on every persisted trace. Admin can purge by tag (right-to-be-forgotten — future endpoint). |

## Configuration cheat sheet

Workstation (full set in `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | The Ollama endpoint (or the Caddy front of it). |
| `OLLAMA_API_KEY` | — | Bearer for the Ollama vhost. |
| `OGCAIBB_MODEL_CHAT` / `_ROUTER` / `_EMBED` | qwen2.5-coder:32b · :7b · nomic-embed-text | Role → model name. |
| `OGCAIBB_HOST` / `OGCAIBB_PORT` / `OGCAIBB_DAEMON_URL` | `127.0.0.1` / `4141` / — | Daemon bind + explicit URL for CLI self-talk. |
| `OGCAIBB_WORKSPACE_ROOT` | cwd | Workspace boundary for file tools. |
| `OGCAIBB_TRACE_ENABLED` | `1` | Master switch for the trace pipeline. |
| `OGCAIBB_TRACE_TRANSPORT` | `noop` | `noop` → local-only; `https` → ship to hub. |
| `OGCAIBB_TRACE_MAX_BYTES` / `_AGE_SECONDS` / `_TOTAL_BYTES` | 4 MiB / 300s / 200 MiB | WAL rotation + disk budget. |
| `OGCAIBB_REDACTOR` / `_RULES` | `ruleset` / — | Active redactor + optional YAML override. |
| `OGCAIBB_HUB_URL` / `OGCAIBB_HUB_AUTH` / `OGCAIBB_HUB_TOKEN` | — / `apikey` / — | Hub endpoint + auth. |
| `OGCAIBB_IMPLICIT_SIGNALS` | `tool_call_completed_clean` | Enabled detectors, comma-sep. |
| `OGCAIBB_IMPLICIT_EDIT_RETENTION_DELAY` / `_GIT_COMMIT_DELAY` | 600 / 1800 | Detector delays in seconds. |
| `OGCAIBB_INJECT_MENU` | `1` | Inject skill/agent/command manifest into system prompt. |
| `OGCAIBB_RETRIEVE_ENABLED` | `0` | Call hub /v1/retrieve before each turn. |
| `OGCAIBB_RETRIEVE_TOP_K` / `_MIN_SCORE` / `_SCOPE` / `_TIMEOUT` | 3 / 0.5 / `all` / 2.0 | Retrieval tuning. |

Hub (full set in `hub/.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OGCAIBB_HUB_HOST` / `OGCAIBB_HUB_PORT` | `127.0.0.1` / `8080` | Bind (Caddy fronts it). |
| `OGCAIBB_HUB_AUTH` | `apikey` | Auth provider. |
| `OGCAIBB_HUB_APIKEYS_FILE` | `/etc/ogcaibb-hub/keys.yaml` | Bearer-key registry. |
| `OGCAIBB_HUB_TRACE_STORE` / `_PATH` | `localfs` / `/var/lib/ogcaibb-hub/chunks` | Raw chunk store. |
| `OGCAIBB_HUB_INDEX_STORE` / `_PATH` | `sqlite` / `/var/lib/ogcaibb-hub/state.db` | Index + signals DB. |
| `OGCAIBB_HUB_EMBEDDER` | — (off) | Set to enable retrieval. |
| `OGCAIBB_HUB_EMBEDDER_HOST` / `_MODEL` / `_API_KEY` | `localhost:11434` / `nomic-embed-text` / — | Embedder endpoint. |
| `OGCAIBB_HUB_VECTOR_STORE` | — (off) | `memory` for dev; `qdrant` for prod. |
| `OGCAIBB_HUB_VECTOR_STORE_URL` / `_COLLECTION` / `_API_KEY` | `localhost:6333` / `ogcaibb-traces` / — | Vector backend config. |
| `OGCAIBB_HUB_MAX_CHUNK_BYTES` | 16 MiB | Hard cap on a single incoming chunk. |

## Test coverage

48 tests passing across both packages, covering:

- Redactor (secret patterns, path denylist, length cap, PEM collapse, immutability)
- WAL (rotation, recovery, mark_uploaded, disk budget)
- Workstation → hub ingest roundtrip (auth, sha256 verify, gz storage, dedupe)
- Feedback endpoint + signal roundtrip
- Each implicit detector (positive/negative/neutral paths)
- SignalObserver scheduling + shutdown
- System prompt resolution (rules always present, menu, exemplars, ordering, idempotency, opt-out)
- Vector store cosine ordering + filters + min-score
- Hub ingest indexes a trace into vectors
- Retrieve roundtrip with fake embedder
- `format_exemplars` rendering

## What's wired vs. deferred

### Wired

- Two-machine deployment (GPU box + proxy+hub)
- OpenAI-compat daemon serving Continue.dev, aider, ogcaibb CLI
- Slash-command parsing, skill activation, agent allowlists
- In-process tools (Read, Write, Edit, Bash, WebFetch, Docker validators, embeddings)
- Trace capture: WAL, redaction, transport, uploader
- Hub ingest with bearer auth + sha256 verify + raw + SQLite index + dedupe
- Explicit feedback endpoint + CLI + hub signals persistence
- Three implicit detectors + observer
- Skill / agent / command menu injection
- Critical rules inherited across every agent
- Embedder + vector store (in-memory + Qdrant)
- `/v1/retrieve` endpoint + workstation pre-turn exemplar injection
- Streaming + non-streaming chat completion both capture traces and emit `trace_id`

### Deferred

The full roadmap — short-term and long-term — plus a comparison against
running opencode (or a similar terminal IDE agent) instead of the custom
daemon, lives in [`FUTURE_WORK.md`](FUTURE_WORK.md). One-line summary of
the headline items:

- **Closing-the-loop substrate** — `FeedbackAggregator`, polarity-aware
  retrieval, `reprompt_correction` detector.
- **Tool-use improvements** — `WriteMany`, skills-as-tools, agent-to-agent
  delegation.
- **Auth + multi-tenancy** — GitHub OAuth, right-to-be-forgotten endpoints.
- **Learning loop** — hub `/v1/distill` cron, local LoRA fine-tuning.
- **Interoperability** — MCP server exposure, possible hybrid with opencode.

### Known limitations

- Trace pipeline ineffective if Local Network privacy not granted to the IDE on macOS (Sequoia/Sonoma) — symptom is the daemon's outbound to a LAN Ollama failing while terminal-launched daemons succeed.
- Daemon ↔ Ollama connection uses a single httpx client created at startup. If Ollama becomes unreachable mid-session and recovers, the daemon's pool may need a recycle (open follow-up).
- Implicit-detector intents are in-memory only — daemon restart mid-window drops pending signals. Acceptable for v0.1; persistence is a small SQLite addition.
- Vector store indexing is synchronous inside `/v1/ingest`. Fine for current chunk sizes; needs async-ification when chunks routinely carry many traces.

## Why this shape

- **No remote LLM, ever.** Hard rule. Daemon returns HTTP 503 if Ollama is gone rather than fall back to a remote provider. Embedder also goes through on-prem Ollama.
- **Hub on the proxy box, not the GPU box.** Keeps the GPU box single-purpose and stateless; the proxy already has the TLS + auth boundary and the disk for raw chunks + SQLite + Qdrant.
- **Protocols + registries everywhere.** Every concrete impl is one config flip away from being swapped. Lets the architecture evolve (Chroma → Qdrant → pgvector; APIKey → GitHub → mTLS) without touching call sites.
- **Capture before retrieve.** The trace pipeline lands before the retrieval loop because retrieval needs a corpus and a feedback signal. With capture + signals running, retrieval becomes the natural next layer; without them it would be groundless.
- **Best-effort retrieval.** Retrieval failure never blocks a chat turn. The fallback is the workstation behaving exactly as it did before retrieval was added.
- **Composable system prompt.** Five layers (intro/body, menu, exemplars, critical rules) with a single chokepoint resolver. Skill and agent manifests can't drop the invariants; clients that don't speak the extension protocol still see the menu.
