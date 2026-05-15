# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  Entry surface                                               │
│   - Continue.dev (VS Code, JetBrains, Zed)                   │
│   - aider                                                    │
│   - ogcaibb CLI                                              │
└─────────────────────┬────────────────────────────────────────┘
                      │  OpenAI-compatible /v1/chat/completions
┌─────────────────────▼────────────────────────────────────────┐
│  Daemon (FastAPI)                                            │
│   ┌────────────────────────────────────────────────────────┐ │
│   │ commands.parser  → expands /slash from commands/*.md   │ │
│   │ skills.registry  → loads SKILL.md frontmatter+body     │ │
│   │ agents.registry  → loads agent manifests + tools list  │ │
│   │ loop.run_turn    → PydanticAI Agent over Ollama        │ │
│   └────────────────────────────────────────────────────────┘ │
└─────────────────────┬────────────────────────────────────────┘
                      │
        ┌─────────────┴───────────────┬──────────────────────┐
        ▼                             ▼                      ▼
┌───────────────┐         ┌────────────────────┐    ┌──────────────────┐
│ Ollama client │         │ Tool registry      │    │ Memory + Chroma  │
│ (httpx)       │         │ (in-process only)  │    │                  │
│ - chat        │         │  - workspace       │    │ - markdown files │
│ - embed       │         │  - web_fetch       │    │ - vector store   │
│ - health      │         │  - docker_runner   │    │                  │
│               │         │  - validators      │    │                  │
│               │         │  - embedding       │    │                  │
└───────────────┘         └────────────────────┘    └──────────────────┘
```

## Request lifecycle

1. Client sends `POST /v1/chat/completions` with messages.
2. Daemon health-checks Ollama. If unreachable → **HTTP 503**, full stop.
3. If the last user message starts with `/`, `commands.parser` loads the
   matching markdown, substitutes `$ARGUMENTS`, and (if frontmatter declares
   `skill:` or `agent:`) attaches them to the request.
4. If a skill is active, its body is injected into the system prompt.
5. If an agent is active, its system prompt + `tools:` allowlist take effect.
6. `loop.run_turn` builds a PydanticAI `Agent` with the chosen model and
   resolved tool set, then runs the user message.
7. The tool-call trace is attached to the response (`response.ogcaibb.tool_calls`)
   for IDE plugins to surface.

## Why a daemon — and not just Continue.dev → Ollama

Continue.dev → Ollama works for chat and code completion. It does not provide:

- Skill activation by name (with prompt injection).
- Agent sub-runs with constrained tool allowlists.
- Docker-backed validators (`bblocks-postprocess`, `pygeoapi`).
- Workspace-scoped file IO and shell sandboxing.
- Memory persistence.
- Slash-command expansion from markdown templates.

All of that lives in the daemon. The daemon presents an OpenAI-compatible
surface so it slots into Continue.dev, Cline, aider, Zed AI, etc. without each
of them needing a custom integration.

## Why in-process tools, not MCP

MCP is valuable when the same tool is consumed by *multiple* hosts. In v1 the
only host is ogcaibb itself, so in-process tools avoid:

- A second process per tool (lifecycle, crash recovery, logging).
- IPC serialisation overhead on hot paths (file IO, schema validation).
- A separate auth model.

Wrapping a tool as an MCP server later is mechanical — the function signatures
in `daemon/src/ogcaibb/tools/*.py` translate directly.

## Why PydanticAI

- First-class Ollama support via the OpenAI provider against `/v1`.
- Typed tool definitions; the model gets a JSON-Schema view automatically.
- Sub-agent composition without ceremony (we use a single Agent per turn for
  now, but the abstraction lets us nest later).
- No LangChain. Smaller dependency footprint.

## Model selection rationale

| Role | Model | Why |
|---|---|---|
| chat / edit | `qwen2.5-coder:32b-instruct` | Strong tool-calling, big context, well-supported by Ollama. |
| autocomplete | `qwen2.5-coder:7b-instruct` | Latency-bound; small model lives in spare VRAM. |
| embeddings | `nomic-embed-text` | 768-dim, fast, good on technical English. |

Larger options (`llama3.3:70b`, `deepseek-coder-v2:236b`) work but need ≥48 GB
VRAM and aren't worth the extra latency for our workload (lots of small tool
turns, not long monologues).

## State

- **Memory** — markdown files under `OGCAIBB_MEMORY_DIR`, same shape as
  Claude Code's `MEMORY.md` index + individual entries.
- **Vectors** — Chroma local persistent client (zero ops), optionally
  swappable to a shared Qdrant for multi-developer corpora.
- **Tasks / plans** — not in v1. Add SQLite when the loop grows multi-turn
  state beyond what the conversation history can carry.

## Security boundary

- Bearer-token auth on the upstream Ollama proxy.
- Daemon binds to `127.0.0.1` by default. Do not expose it to a LAN without
  putting an auth proxy in front.
- Workspace boundary on every filesystem tool.
- `Bash` runs under `sandbox-exec` (macOS) / `firejail` (Linux) when available;
  it warns and runs unsandboxed otherwise. Production deployments should
  install one of the two.
- `WebFetch` defaults to a marine-vocabulary allowlist. Override with
  `OGCAIBB_WEB_ALLOW_ALL=1`.

## Deferred

- Streaming SSE responses (Continue.dev works fine on non-streaming for now).
- MCP server exposure.
- Multi-user daemon (one workspace per process today).
- The marine data adapters (HELCOM/EMODnet/ICES) — skills are present, the
  thin OWSLib wrappers aren't yet.
- The bblock-catalog walker (skill present, walker not in `tools/` yet).
