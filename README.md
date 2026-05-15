# ogcaibb — local AI toolset for OGC Building Block repositories

A local daemon plus a curated set of agents, skills, and slash commands that
makes working on any [OGC Building Block](https://opengeospatial.github.io/bblocks/)
repository feel like Claude Code — but backed by a shared on-prem Ollama
server, with **no fallback to remote LLM providers**.

```
┌──────────────────────────────────────────────────────────────┐
│  Shared server (managed by you)                              │
│    Ollama  ──  qwen2.5-coder:32b · nomic-embed-text          │
│    Caddy/Traefik in front (TLS + bearer-token auth)          │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTPS, Authorization: Bearer …
┌────────────────────────▼─────────────────────────────────────┐
│  Developer workstation                                       │
│    Continue.dev (VS Code) ─┐                                 │
│    aider (terminal)        │── OpenAI-compatible /v1 ──┐     │
│                            │                          ▼     │
│                            │       ogcaibb daemon            │
│                            │   (FastAPI + PydanticAI)        │
│                            │     · agents / skills /         │
│                            │       commands / memory         │
│                            │     · in-process tools          │
│                            │       (Read/Write/Edit/Bash,    │
│                            │        Docker validators,       │
│                            │        OGC clients, embeddings) │
└────────────────────────────┴─────────────────────────────────┘
```

## Repo layout

```
ogcaibb/
├── agents/                # role-scoped Claude-Code-style agents
│   ├── integrator/
│   ├── converters/
│   ├── generators/
│   └── validators/
├── skills/                # SKILL.md files with YAML frontmatter
├── commands/              # slash-command markdown templates
├── clients/
│   ├── continue/          # Continue.dev config
│   └── aider/             # aider config
└── daemon/
    └── src/ogcaibb/
        ├── main.py        # FastAPI + /v1/chat/completions
        ├── loop.py        # PydanticAI agent loop
        ├── ollama_client.py
        ├── config.py
        ├── skills/        # skill registry
        ├── agents/        # agent registry
        ├── commands/      # slash-command parser
        └── tools/         # in-process tools
```

## Quick start

```bash
git clone https://github.com/pzaborowski/ogcaibb.git
cd ogcaibb

# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Point at the shared Ollama server
cp .env.example .env
# edit .env: OLLAMA_HOST, OLLAMA_API_KEY, OGCAIBB_WORKSPACE_ROOT

# 3. Sanity check
ogcaibb ping
ogcaibb skills
ogcaibb agents
ogcaibb commands

# 4. Run the daemon
ogcaibb serve
# → listening on http://127.0.0.1:4141

# 5. Wire up a client
cp clients/continue/config.yaml ~/.continue/config.yaml
# reload VS Code; pick ogcaibb-chat from the model dropdown
```

## Using it from a bblock repo

The daemon is workspace-agnostic. To work on a specific bblock repo, point
`OGCAIBB_WORKSPACE_ROOT` at it before starting `ogcaibb serve`:

```bash
OGCAIBB_WORKSPACE_ROOT=/Users/you/repos/iliad-apis-features ogcaibb serve
```

Any tool that touches the filesystem refuses paths outside that root.

## Logs

`ogcaibb serve` writes to stdout/stderr (uvicorn defaults). It does **not**
write to a file on its own. Capture it however you want:

```bash
# foreground — logs appear in the terminal you ran it from
./.venv/bin/ogcaibb serve

# background — redirect to a file you choose
./.venv/bin/ogcaibb serve > /tmp/ogcaibb.log 2>&1 &
tail -f /tmp/ogcaibb.log

# under launchd / systemd, let the supervisor handle log rotation
```

Verbosity is controlled by `OGCAIBB_LOG_LEVEL` in `.env` (`DEBUG`, `INFO`,
`WARNING`, `ERROR`).

## Hard rules

- **No remote-LLM fallback.** The daemon talks only to the configured Ollama
  host. If unreachable it returns HTTP 503; it never silently routes elsewhere.
- **Model is server-decided.** Clients pick a *role* (chat / autocomplete /
  embed) and the daemon picks the model. The client's "model" string is
  ignored unless it matches one already configured.
- **Tools are allowlisted per agent.** If an agent's frontmatter lists `tools:
  Read, Bash`, that's the only set the model will see during a sub-run.
- **Workspace boundary.** All file tools resolve paths relative to
  `OGCAIBB_WORKSPACE_ROOT` and refuse anything outside it.

## Server-side Ollama notes

```ini
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
```

```caddy
# Caddyfile in front of Ollama
ollama.example.org {
    @authorized header Authorization "Bearer {env.SHARED_TOKEN}"
    handle @authorized {
        reverse_proxy localhost:11434
    }
    respond 401
}
```

Pull the models once on the server:

```bash
ollama pull qwen2.5-coder:32b-instruct
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

## Status

v0.1.0 — daemon, registries, in-process tools (`Read`, `Write`, `Edit`, `Bash`,
`WebFetch`, `BblocksPostprocess`, `Pygeoapi{Up,Down}`, `ValidateAgainstSchema`,
`CheckContextCompleteness`, `EmbedUpsert`, `EmbedQuery`). Token-by-token SSE
streaming with separate `reasoning_content` (chain of thought from qwen3-style
reasoning models) and `tool_calls` deltas; client-disconnect aware.

Not yet wired: the marine data adapters (HELCOM/EMODnet/ICES via OWSLib), the
bblock-catalog walker, agent-to-agent delegation tool, MCP exposure.
