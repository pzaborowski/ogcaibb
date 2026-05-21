# Runbook — running each component

Step-by-step setup for the three deployment roles plus the optional pieces.
For the design picture and trade-offs, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

```
┌──────────────────┐         ┌──────────────────────┐         ┌────────────────────┐
│  GPU box         │         │  Proxy + hub box     │         │  Developer machine │
│  Ollama          │◀────────│  Caddy + ogcaibb-hub │◀────────│  ogcaibb daemon    │
│                  │ private │  + Qdrant (opt.)     │ HTTPS   │  + Continue.dev /  │
│                  │  VLAN   │                      │ Bearer  │  aider             │
└──────────────────┘         └──────────────────────┘         └────────────────────┘
```

You can run all three on one machine for local development — just set every
host to `localhost`. Production has them split as drawn.

---

## 1. GPU box — Ollama

Single purpose: serve chat + embed models over HTTP on the private network.

### Prerequisites

- A machine with a GPU big enough for your target chat model
  (`qwen2.5-coder:32b` needs ~24 GB VRAM; `:14b` works in ~12 GB).
- Outbound HTTPS for the initial `ollama pull`.
- Private-network reachability from the proxy box (VLAN, WireGuard, or
  same-VPC).
- macOS / Linux. (Windows works but the systemd / sandbox notes below
  assume Linux.)

### Install

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama
```

### Configure

Bind to the private interface and keep models loaded so the first request
isn't slow.

`sudo systemctl edit ollama.service` (Linux):

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
```

`sudo systemctl daemon-reload && sudo systemctl restart ollama`

### Pull the models

```bash
ollama pull qwen2.5-coder:32b-instruct     # primary chat / edit
ollama pull qwen2.5-coder:7b-instruct      # autocomplete / router
ollama pull nomic-embed-text               # retrieval embeddings
```

If your GPU is small, swap the chat model — the daemon side reads it from
`OGCAIBB_MODEL_CHAT`, no code change needed.

### Verify

From the GPU box itself:

```bash
curl http://localhost:11434/api/tags    # → {"models":[…]}
```

From the proxy box (same VPC):

```bash
curl http://gpu-host.internal:11434/api/tags
```

If this fails: check `OLLAMA_HOST` actually binds non-localhost; check the
private-network route; check no host firewall is blocking 11434.

### Logs

```bash
journalctl -u ollama -f
```

---

## 2. Proxy + hub box — Caddy, ogcaibb-hub, Qdrant (optional)

This box terminates TLS for both the Ollama vhost and the hub vhost, runs the
`ogcaibb-hub` service, and (optionally) hosts Qdrant for vector retrieval.

### Prerequisites

- A small machine (no GPU needed). Disk room for raw trace chunks
  (~80 MB / dev / day) and Qdrant index. A 50 GB volume covers ~2 years
  comfortably for a 10-dev team.
- Routable hostname(s) for `ollama.example.org` and `hub.example.org` with
  Let's Encrypt-eligible DNS.
- Private-network reachability to the GPU box (see § 1).
- Python 3.11+ and `uv` (or `pip`).

### Install Caddy

```bash
# Debian / Ubuntu
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

### Caddyfile

`/etc/caddy/Caddyfile`:

```caddy
(auth) {
    @authorized header Authorization "Bearer {env.OGCAIBB_TOKEN}"
}

ollama.example.org {
    import auth
    handle @authorized {
        reverse_proxy gpu-host.internal:11434
    }
    respond 401
}

hub.example.org {
    import auth
    handle @authorized {
        reverse_proxy localhost:8080
    }
    respond 401
}
```

`/etc/caddy/env` (or systemd drop-in) provides `OGCAIBB_TOKEN`. Keep it
secret; the Bearer-presence check is one of two auth layers (the hub
re-validates it against `keys.yaml`).

```bash
sudo systemctl restart caddy
sudo systemctl status caddy
```

### Install ogcaibb-hub

```bash
git clone https://github.com/pzaborowski/ogcaibb.git
cd ogcaibb/hub
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
# Optional: enable Qdrant vector store
uv pip install -e ".[dev,qdrant]"
```

### Configure ogcaibb-hub

`/etc/ogcaibb-hub/.env`:

```ini
OGCAIBB_HUB_HOST=127.0.0.1
OGCAIBB_HUB_PORT=8080
OGCAIBB_HUB_LOG_LEVEL=INFO

OGCAIBB_HUB_AUTH=apikey
OGCAIBB_HUB_APIKEYS_FILE=/etc/ogcaibb-hub/keys.yaml

OGCAIBB_HUB_TRACE_STORE=localfs
OGCAIBB_HUB_TRACE_STORE_PATH=/var/lib/ogcaibb-hub/chunks

OGCAIBB_HUB_INDEX_STORE=sqlite
OGCAIBB_HUB_INDEX_STORE_PATH=/var/lib/ogcaibb-hub/state.db

# --- retrieval (set BOTH to enable; leave both unset to disable) ---
OGCAIBB_HUB_EMBEDDER=ollama
OGCAIBB_HUB_EMBEDDER_HOST=http://gpu-host.internal:11434
OGCAIBB_HUB_EMBEDDER_MODEL=nomic-embed-text

OGCAIBB_HUB_VECTOR_STORE=qdrant          # or 'memory' for dev
OGCAIBB_HUB_VECTOR_STORE_URL=http://localhost:6333
OGCAIBB_HUB_VECTOR_STORE_COLLECTION=ogcaibb-traces
```

```bash
sudo mkdir -p /etc/ogcaibb-hub /var/lib/ogcaibb-hub/chunks
sudo touch /etc/ogcaibb-hub/keys.yaml
sudo chown -R ogcaibb-hub:ogcaibb-hub /var/lib/ogcaibb-hub
```

### Issue developer keys (manual, server-side)

```bash
ogcaibb-hub issue-key --name piotr-laptop
# prints:
#   Token (give to the developer, store nowhere else):
#     ogcaibb_pat_…
#   Append to /etc/ogcaibb-hub/keys.yaml:
#     keys:
#       - name: piotr-laptop
#         sub: apikey:piotr-laptop
#         key_sha256: …
#         revoked: false
```

Paste the YAML snippet into `keys.yaml`. The hub mtime-watches the file —
new entries take effect within ~5 seconds without a restart. To revoke a key,
flip `revoked: true` (no delete needed).

List what's currently active:

```bash
ogcaibb-hub list-keys
```

### Run Qdrant (optional, only if `OGCAIBB_HUB_VECTOR_STORE=qdrant`)

```bash
docker run -d --restart=always \
  -p 127.0.0.1:6333:6333 \
  -p 127.0.0.1:6334:6334 \
  -v /var/lib/ogcaibb-hub/qdrant:/qdrant/storage \
  --name qdrant qdrant/qdrant
```

The hub auto-creates the collection on first upsert with the embedder's
detected vector dimension.

### Run ogcaibb-hub as a systemd unit

`/etc/systemd/system/ogcaibb-hub.service`:

```ini
[Unit]
Description=ogcaibb-hub ingestion + retrieval
After=network-online.target

[Service]
User=ogcaibb-hub
Group=ogcaibb-hub
WorkingDirectory=/opt/ogcaibb/hub
EnvironmentFile=/etc/ogcaibb-hub/.env
ExecStart=/opt/ogcaibb/hub/.venv/bin/ogcaibb-hub serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ogcaibb-hub
sudo systemctl status ogcaibb-hub
journalctl -u ogcaibb-hub -f
```

### Verify

From the proxy box:

```bash
curl http://127.0.0.1:8080/healthz             # local, unauth → 200 ok
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/v1/retrieve \          # bypass Caddy
  -X POST -H 'content-type: application/json' \
  -d '{"query":"test"}'
```

From the internet:

```bash
curl https://hub.example.org/healthz           # 401 (Caddy enforces Bearer)
curl -H "Authorization: Bearer <token>" \
  https://hub.example.org/healthz              # 200 ok
```

### Inspect what's been ingested

```bash
ogcaibb-hub count                              # row counts in the index
sqlite3 /var/lib/ogcaibb-hub/state.db \
  'SELECT workstation_id, COUNT(*) FROM traces GROUP BY 1'
ls /var/lib/ogcaibb-hub/chunks/                # one subdir per workstation
```

---

## 3. Developer workstation — ogcaibb daemon + IDE clients

The daemon is a long-running local process on `127.0.0.1:4141`. It speaks
the OpenAI chat-completions protocol so any IDE that talks to OpenAI can
point at it directly.

### Prerequisites

- Python 3.11+ and `uv`.
- Reachability to `ollama.example.org` and `hub.example.org` (or to the
  same boxes directly if you're testing on the LAN).
- On macOS Sequoia/Sonoma: **System Settings → Privacy & Security → Local
  Network** must allow VSCode / Cursor / your IDE, otherwise daemon
  subprocesses launched from the IDE can't reach LAN hosts. (Terminal.app
  has its own grant; this only bites IDE-launched runs.)

### Install

```bash
git clone https://github.com/pzaborowski/ogcaibb.git
cd ogcaibb
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
$EDITOR .env
```

Minimum settings to set:

```ini
OLLAMA_HOST=https://ollama.example.org
OLLAMA_API_KEY=<the same shared bearer Caddy enforces>

OGCAIBB_WORKSPACE_ROOT=/Users/you/repos/some-bblock-repo

# To ship traces to the hub (recommended once hub is up):
OGCAIBB_HUB_URL=https://hub.example.org
OGCAIBB_HUB_TOKEN=ogcaibb_pat_…            # from `ogcaibb-hub issue-key`
OGCAIBB_TRACE_TRANSPORT=https              # default 'noop' keeps everything local

# To enable retrieval (hub must have embedder + vector_store configured):
OGCAIBB_RETRIEVE_ENABLED=1
```

Defaults that are usually fine:

- `OGCAIBB_MODEL_CHAT=qwen2.5-coder:32b-instruct`
- `OGCAIBB_IMPLICIT_SIGNALS=tool_call_completed_clean` (opt-in to the
  IO-heavy ones via `tool_call_completed_clean,edit_retention,git_commit`)
- `OGCAIBB_INJECT_MENU=1` (skills/agents/commands manifest in the system
  prompt)

### Sanity-check Ollama from the workstation

```bash
ogcaibb ping                # → {"reachable": true, "host": ..., "installed": [...]}
ogcaibb skills              # list discovered skills
ogcaibb agents              # list discovered agents
ogcaibb commands            # list discovered slash commands
```

If `ogcaibb ping` fails: see the architecture doc's "Known limitations" — on
macOS the most common cause is the IDE not having Local Network privacy.

### Run the daemon

Foreground (logs to the terminal):

```bash
ogcaibb serve
# → INFO: Uvicorn running on http://127.0.0.1:4141
```

Background:

```bash
./.venv/bin/ogcaibb serve > /tmp/ogcaibb.log 2>&1 &
tail -f /tmp/ogcaibb.log
```

The daemon needs to be running for any IDE plugin to work. Restart it after
editing `.env` (settings load at import time).

### Wire up Continue.dev (VS Code / JetBrains / Zed)

```bash
cp clients/continue/config.yaml ~/.continue/config.yaml
```

Reload the IDE; pick `ogcaibb-chat` from the model dropdown. Confirm with a
trivial chat — the daemon log should show `POST /v1/chat/completions 200`.

### Wire up aider (terminal)

```bash
cp clients/aider/.aider.conf.yml ~/.aider.conf.yml
aider                                # uses http://localhost:4141/v1
```

### macOS launchd (optional auto-start)

`~/Library/LaunchAgents/com.ogcaibb.daemon.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ogcaibb.daemon</string>
  <key>WorkingDirectory</key><string>/Users/you/repos/ogcaibb</string>
  <key>EnvironmentVariables</key>
  <dict><key>OGCAIBB_WORKSPACE_ROOT</key><string>/Users/you/repos/some-bblock-repo</string></dict>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/repos/ogcaibb/.venv/bin/ogcaibb</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ogcaibb.log</string>
  <key>StandardErrorPath</key><string>/tmp/ogcaibb.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.ogcaibb.daemon.plist
launchctl list | grep ogcaibb
```

### Linux systemd (--user) (optional)

`~/.config/systemd/user/ogcaibb.service`:

```ini
[Unit]
Description=ogcaibb daemon
After=network-online.target

[Service]
WorkingDirectory=%h/repos/ogcaibb
EnvironmentFile=%h/repos/ogcaibb/.env
ExecStart=%h/repos/ogcaibb/.venv/bin/ogcaibb serve
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now ogcaibb
journalctl --user -u ogcaibb -f
```

### Inspect what's captured locally

```bash
ogcaibb traces status                  # WAL stats (sealed/open chunks, bytes)
ogcaibb traces tail -n 5               # last 5 trace records
ogcaibb traces tail -n 1 --messages    # with full input messages
```

### Send explicit feedback

When the assistant produced something good (or bad), record it against the
`trace_id` from the response:

```bash
ogcaibb feedback <trace_id> --rating up --comment "kept the diff"
ogcaibb feedback <trace_id> --rating down --comment "wrong vocab"
```

The `trace_id` is in the OpenAI response:

```bash
curl -sS http://127.0.0.1:4141/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"say hi"}]}' | jq '.ogcaibb.trace_id'
```

…or in every streaming chunk as `ogcaibb_trace_id`.

---

## 4. Health checks at a glance

| Where | Command | Expect |
|---|---|---|
| GPU box | `curl http://localhost:11434/api/tags` | 200 + model list |
| Proxy → GPU (private) | `curl http://gpu-host.internal:11434/api/tags` | 200 + model list |
| Proxy → hub (local) | `curl http://127.0.0.1:8080/healthz` | 200 ok |
| Internet → Ollama (TLS+auth) | `curl -H "Authorization: Bearer …" https://ollama.example.org/api/tags` | 200 |
| Internet → hub (TLS+auth) | `curl -H "Authorization: Bearer …" https://hub.example.org/healthz` | 200 |
| Workstation → daemon | `curl http://127.0.0.1:4141/healthz` | 200 + `reachable: true` |
| Workstation → daemon chat | `curl -sS http://127.0.0.1:4141/v1/chat/completions -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}'` | 200 |

If `healthz` returns 503 from the workstation daemon, the detail message
contains the actual Ollama-reachability error.

---

## 5. Operator CLI reference

### Workstation: `ogcaibb`

| Command | Purpose |
|---|---|
| `ogcaibb serve` | Start the daemon (FastAPI + agent loop). |
| `ogcaibb ping` | Health-check the configured Ollama host. |
| `ogcaibb skills` | List discovered skills. |
| `ogcaibb agents` | List discovered agents. |
| `ogcaibb commands` | List discovered slash commands. |
| `ogcaibb traces status` | WAL stats: open chunk, sealed backlog, uploaded count. |
| `ogcaibb traces tail [-n N] [--messages]` | Recent trace records from open + sealed chunks. |
| `ogcaibb feedback <trace_id> [--rating up\|down\|neutral] [--comment]` | Explicit rating against a captured trace. |

### Hub: `ogcaibb-hub`

| Command | Purpose |
|---|---|
| `ogcaibb-hub serve` | Start the hub HTTP server. |
| `ogcaibb-hub issue-key --name <label> [--sub <sub>]` | Generate a new bearer token, prints the YAML snippet to append to `keys.yaml`. Token is printed once and never stored. |
| `ogcaibb-hub list-keys` | List entries in `keys.yaml` (sha256 prefix only — no tokens). |
| `ogcaibb-hub count` | Show row counts in the index store. |

---

## 6. Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| Daemon 503 with "Ollama host … unreachable: All connection attempts failed" | Network reach (the umbrella message hides per-address OS errors) | Check `route get <ollama-ip>` and proxy env vars. On macOS check Local Network privacy for the IDE. |
| Daemon works from Terminal but not from VSCode | macOS Local Network privacy not granted to VSCode (Code Helper subprocesses inherit denial) | System Settings → Privacy & Security → Local Network → enable VSCode, then **fully quit + reopen**. |
| `ogcaibb traces tail` returns no records | Trace pipeline not active | Check `OGCAIBB_TRACE_ENABLED=1` and confirm in the daemon's startup log that "trace capture enabled (…)" appears. |
| Sealed chunks accumulate but never upload | Hub unreachable, auth rejected, or schema mismatch | Daemon log shows the transport error and whether it's retriable. `lsof` the daemon for httpx connections. |
| Hub returns 401 on every request | Token sha256 doesn't match any entry in `keys.yaml` | Re-issue and update the entry; confirm with `ogcaibb-hub list-keys`. |
| Hub `/v1/retrieve` returns 503 | Embedder or vector_store not configured | Set both `OGCAIBB_HUB_EMBEDDER` and `OGCAIBB_HUB_VECTOR_STORE`. Restart the hub. |
| Model only writes one file then stops | Small model interpreting first successful Write as a stopping signal | Use a bigger chat model. The critical-rules block already mandates per-file verification — but small model often ignores it. `qwen2.5-coder:14b` or `:32b` are reliable. |
| Streaming reasoning/thoughts missing | Model size (small model skips `<think>` under load) OR menu pushed prompt too large | Try `OGCAIBB_INJECT_MENU=0` to isolate; or upgrade chat model. |
| `ogcaibb-hub count` is 0 after many chat turns | Trace transport still `noop` | Set `OGCAIBB_TRACE_TRANSPORT=https` on the workstation, restart, take one turn, check again. | 
