# Future work + architectural alternatives

This is the forward-looking companion to [`ARCHITECTURE.md`](ARCHITECTURE.md)
(which describes the system as built) and [`RUNBOOK.md`](RUNBOOK.md) (which
describes how to run it). The two documents above stay narrow; this one is
broader and explicitly tentative — the place to capture trade-offs we
revisited and to track work we haven't picked up yet.

Sections:

1. [Architectural alternatives — opencode (and the hybrid path)](#1-architectural-alternatives--opencode-and-the-hybrid-path)
2. [Short-term roadmap](#2-short-term-roadmap)
3. [Long-term / structural work](#3-long-term--structural-work)
4. [Operational gaps](#4-operational-gaps)
5. [Open questions](#5-open-questions)

---

## 1. Architectural alternatives — opencode (and the hybrid path)

A question that surfaces periodically: *should we have built this on top of
opencode (or a similar terminal coding agent) instead of running a custom
daemon?* The honest comparison is below.

### What opencode (and similar IDE agents) cover well

- **Tool-use loop maturity.** opencode / Claude Code / aider have years of
  refinement around tool calls, retries, partial outputs, recovery from
  malformed JSON. PydanticAI + our loop is fine; objectively younger.
- **MCP ecosystem.** Dozens of off-the-shelf tools (filesystem, git, browser,
  SQL, search, …) plug in via config. We'd have to write each as an in-process
  tool today.
- **TUI / session management / multi-provider abstraction.** None of which
  we built or care about for an IDE-centric workflow.
- **Skills / agents / slash commands.** opencode has equivalents and its
  formats are close enough to Claude Code's that our existing `skills/`,
  `agents/`, `commands/` would port with light frontmatter edits.
- **Multi-developer onboarding.** Single `brew install opencode-ai` vs.
  our six-step daemon install.

### What opencode doesn't cover — the load-bearing reasons ogcaibb exists

Every item below is a structural gap for a team learning system, not a
feature opencode could add by toggling a setting:

- **Trace capture + WAL + uploader to a shared hub.** opencode runs entirely
  on the developer's box; there is no concept of "ship the team's interactions
  to a central service for learning." This is the load-bearing piece of our
  work and has no analogue.
- **Hub-side ingest + indexing + vector retrieval.** opencode has no server.
  The hub is its own product whether or not the workstation runs the daemon.
- **Implicit feedback detectors** (`git_commit`, `edit_retention`,
  `tool_call_completed_clean`). They observe side-effects of completed turns
  and emit signals. Not present in any IDE-only agent.
- **Hard "no remote LLM fallback" rule.** opencode supports local Ollama, but
  as one provider among many. Enforcing "never another provider" is a
  configuration discipline there; it's an architectural invariant here.
- **Workspace boundary on file tools + sandboxed `Bash` by default.** opencode
  trusts its own process. Our tools refuse paths outside
  `OGCAIBB_WORKSPACE_ROOT` and `Bash` runs under `sandbox-exec` (macOS) /
  `firejail` (Linux) by default.
- **System-prompt composition with inherited invariants.** opencode owns its
  prompt; you can prepend via config but not enforce "agents can't drop these
  rules" — the bug we fixed for small models silently regressing on
  Write-tool-use when an agent's body replaced the daemon's default rules.

### The hybrid path

Strictly better than either pure option, if the rebuild cost is acceptable:

```
┌────────────────────┐         ┌──────────────────────┐         ┌────────────────────┐
│ GPU box: Ollama    │         │ Proxy + hub box      │         │ Developer machine  │
│ (unchanged)        │◀────────│ Caddy + ogcaibb-hub  │◀────────│ opencode CLI       │
│                    │         │ + Qdrant             │  MCP    │ + retrieval MCP    │
│                    │         │ (unchanged)          │ tools   │ + trace-hook MCP   │
└────────────────────┘         └──────────────────────┘         └────────────────────┘
```

- **Keep** the hub. Nothing in opencode replaces it; centralized team learning
  is a service architecture, not a CLI feature.
- **Drop** the workstation daemon (FastAPI, OpenAI-compat surface, agent
  loop, menu injection).
- **Replace with** two small MCP servers:
  - **`ogcaibb-retrieve-mcp`** — one tool, `retrieve_exemplars(query)`,
    proxies to hub `/v1/retrieve`. opencode calls it before-or-during a turn.
  - **`ogcaibb-trace-mcp`** — hooks into opencode's per-turn lifecycle and
    POSTs a redacted trace envelope to hub `/v1/ingest`. (Verify which
    lifecycle hooks opencode exposes before committing.)
- **Convert** `skills/`, `agents/`, `commands/` to opencode's equivalents.
  Mechanical translation, mostly frontmatter renames.
- **Lose** the critical-rules-always-appended chokepoint. opencode lets you
  prepend system-prompt content but doesn't have the same "agents can't drop
  invariants" guarantee. The fallback is documenting the rules in every
  agent body or using opencode's project-level prompt config.

### Cost / payoff

| Path | Cost | Payoff |
|---|---|---|
| **Stay on ogcaibb daemon** | Maintain ~3 kLoc, slower tool-use evolution | Full control, no third-party CLI dependency, every invariant enforceable |
| **Migrate to opencode + MCP** | ~1 week port + ongoing dependency on opencode's roadmap | Better tool-use, MCP ecosystem, less code to own |
| **Run both in parallel** | None, opencode and the daemon don't conflict | Devs pick whichever they prefer; both can ship traces to the same hub once the MCP trace-hook exists |

### Recommendation

If we started fresh today with no daemon, the right call would be **opencode +
MCP + our hub**. The tool-use loop and ecosystem are too much value to leave
on the table when there's no compelling reason to own them.

Given the daemon exists, is integrated with Continue.dev and aider, and the
trace + retrieval substrate runs through it, the migration only pays off if:

1. We want to expand into tasks opencode handles natively (full-repo
   refactors, multi-file edits at scale, MCP-tool-heavy workflows), or
2. The PydanticAI tool-use becomes a real bottleneck. Small-model multi-Write
   failures (model dropping into "paste files as markdown" mode) are an
   early warning. The `WriteMany` batched-write tool is a cheaper fix for
   that specific failure.

Otherwise, the lowest-regret path is **keep the daemon for the OpenAI-compat
surface** that Continue.dev/aider use today, and **let curious developers
also run opencode** pointed at the same local Ollama. Both can ship traces
to the same hub once the trace-hook MCP exists. Nothing to migrate; hub
keeps accumulating learning data from both clients.

### Concrete pre-migration step

Before committing to anything, the highest-leverage exploration is to
**write `ogcaibb-trace-mcp` as a standalone tool**. ~200 lines of code,
no daemon changes needed, lets a developer try opencode against the same
hub for a week. If the traces look comparable to the daemon's, the
migration is mechanical. If they look worse (less context, missing
tool-call detail), the daemon's value is concrete.

---

## 2. Short-term roadmap

In rough priority order — the items most likely to land next.

### Closing-the-loop substrate

- **`FeedbackAggregator` on the hub.** Combine explicit ratings + implicit
  signals into one per-trace rating, persisted alongside the trace row.
  Default impl: `WeightedSum` heuristic (explicit ratings short-circuit;
  otherwise sum `polarity * weight` across signals). Pluggable so a learned
  model can replace it once we have labeled data.
- **Polarity-aware retrieval.** `/v1/retrieve` filters out negatively-rated
  traces and weights ranking by polarity. Currently retrieves everything
  indexed, ranks by cosine only. Cheap follow-up once the aggregator lands.
- **`reprompt_correction` implicit detector.** The missing third detector
  from the original design. Needs cross-turn conversation tracking on the
  workstation: hash the previous N user messages, match incoming history,
  classify next user message as a correction (regex + small Ollama prompt).
  Strong negative-signal source.

### Tool-use improvements

- **`WriteMany` batched-write tool.** One tool call, list of `{path, content}`
  pairs, atomic per-file write. Pragmatic fix for small models that fan out
  reliably under single tool calls but flake on N sequential `Write`s.
  Roughly 80 lines.
- **Skills-as-tools** (`ListSkills` / `LoadSkill`). Durable alternative to
  the menu: the model loads a skill body on demand mid-turn instead of seeing
  only its description. Two in-process tools + auto-include in every agent's
  allowlist. The menu becomes a fallback for clients that don't tool-call.
- **Agent-to-agent delegation tool.** What `marine-workflow-orchestrator` and
  similar coordinator agents actually need. Reuses the per-agent tool
  allowlist; new tool `Delegate(subagent_name, task)` builds a fresh
  PydanticAI Agent from the named subagent's manifest, runs it, returns its
  final text. Recursion depth cap (e.g. `OGCAIBB_MAX_DELEGATION_DEPTH=2`).

### Operational

- **Async background indexer on the hub.** Embeds run inside `/v1/ingest`
  today. Fine at current chunk sizes; refactor to a queue + worker when
  ingest chunks routinely carry many traces. Pattern: SQLite job table +
  asyncio worker pulling unindexed `trace_id`s.
- **Persistent detector intents.** Currently in-memory; daemon restart
  mid-window drops pending signals. SQLite addition next to the WAL
  manifest; on startup re-schedule any detector whose due time hasn't passed.
- **Hub `/v1/feedback` direct endpoint.** Today explicit feedback goes
  workstation daemon → WAL → uploader → hub /v1/ingest. A direct endpoint
  on the hub would let non-daemon clients (web UIs, Slack bots) rate traces
  without going through the WAL pipeline. Same `Identity` middleware,
  same `SignalRow` upsert.

---

## 3. Long-term / structural work

### Auth + multi-tenancy

- **GitHub OAuth as a second `HubAuth`.** Device-flow on workstation first
  run, hub verifies via `https://api.github.com/user` (cached 5 min). Slots
  beside `APIKeyAuth` behind the existing Protocol. Optional org-membership
  check for access gating.
- **Identity-aware admin endpoints.** `DELETE /v1/identities/{sub}/traces`
  for right-to-be-forgotten; `GET /v1/identities/{sub}/usage` for per-dev
  audit. Reuses the `Identity.sub` tag already on every persisted trace.
- **Hub-side rate limits per identity.** Per-token budget on `/v1/ingest`
  and `/v1/retrieve`, sliding window. Not pressing at current scale.

### Learning loop

- **Hub `/v1/distill` cron.** Periodic job: cluster positive-rated traces
  per skill/agent, draft SKILL.md / agent-prompt updates, open PRs against
  a curated git repo that workstations `git pull` from. Out of scope until
  ≥1k rated traces accumulate per use case.
- **Local LoRA fine-tuning loop.** Weekly job on the GPU box: rated traces
  → instruction pairs → LoRA via unsloth/axolotl → merged GGUF →
  `ollama create qwen2.5-coder-ogc:32b -f Modelfile` → eval-gated promotion
  via a held-out set. Real cost is the eval harness, not the training.

### Interoperability

- **MCP server exposure of our in-process tools.** Wrap each `tools/*.py`
  function as an MCP server. The function signatures translate directly.
  Useful if/when we want to support non-daemon clients (opencode being the
  motivating example — see § 1).
- **Skill bodies served via tool, not prompt.** Today the menu injects names
  + descriptions only; if the model wants the full body it can't see it.
  Pair this with `LoadSkill` to make skill content fetchable mid-turn.

### Privacy

- **`.ogcaibbignore` per workspace.** Pathspec semantics matching `.gitignore`.
  Reuse the `pathspec` library. Useful when a repo has sensitive directories
  the redactor's defaults don't catch.
- **Per-skill trace opt-out.** `trace: false` frontmatter in SKILL.md for
  brittle flows. Today the only switch is daemon-wide (`OGCAIBB_TRACE_ENABLED=0`).
- **Model-based redactor as a second impl.** Use a tiny local model to flag
  candidate PII / secrets the regex impl misses. Behind the same Protocol.

---

## 4. Operational gaps

Things that currently work but need attention if scale grows.

- **Httpx client recycling on Ollama recovery.** The OllamaClient is a single
  module-level singleton created at lifespan startup. If Ollama becomes
  unreachable mid-session and then recovers, the pool may stay in a bad state
  until the daemon restarts. Pattern: detect `httpx.ConnectError` on a request
  → close + recreate the client → retry once.
- **Daemon → hub backpressure.** Today the WAL caps at 200 MiB and drops the
  oldest sealed chunk if exceeded. There's no surface to surface this to the
  developer; the only signal is a WARN line in the daemon log. Add a healthz
  field reporting current backlog + dropped count.
- **Hub cold-start re-index.** If `state.db` is wiped but raw chunks remain,
  there's no command to re-ingest them. Add `ogcaibb-hub reindex` that walks
  `/var/lib/ogcaibb-hub/chunks/` and re-runs the indexer.
- **macOS Local Network privacy onboarding.** The `route get` succeeds and
  curl succeeds, but VSCode-launched daemons can't reach LAN Ollama until
  the IDE has the Local Network grant. Document this prominently in the
  workstation onboarding (already noted in RUNBOOK § 3, could be a startup
  warning printed by `ogcaibb ping` when it detects a private-network host).
- **Trace pipeline observability.** A `/metrics` Prometheus endpoint on both
  daemon and hub. WAL size, upload rate, retrieve latency, embedder dim,
  index store row counts.

---

## 5. Open questions

Decisions deferred during the build; revisit when each becomes load-bearing.

- **Workstation identity stability across upgrades.** UUID in
  `~/.local/share/ogcaibb/workstation_id` survives daemon upgrades but not
  user-directory wipes or new machines. Should we tie identity to git
  `user.email` (mutable but attributable) or to a hardware identifier?
  Privacy vs. attribution trade-off; deferred until distillation cares.
- **Default retrieval scope.** Currently `all` — anyone's accepted traces
  inform anyone's prompts. Should it default to `team` (via org membership)
  or `self` (only your own past turns)? Affects how much shared learning
  developers feel. Probably revisit when GitHub OAuth lands and team
  membership is queryable.
- **Implicit signal defaults.** Today only `tool_call_completed_clean` is
  on by default; the IO-heavy detectors are opt-in. Should `edit_retention`
  flip to default-on once it has been observed in production for a few
  weeks? Depends on noise rate.
- **Vector store: stay on Qdrant or move to pgvector?** Qdrant chosen because
  it scales past Chroma's single-writer limit and runs as one binary. If we
  end up adding SQL-shaped queries (joins against signal tables, polarity
  filters, dedupe-by-prompt-hash), pgvector + Postgres becomes attractive
  since the index store is already SQLite-shaped. Revisit when polarity-
  aware retrieval is on the table.
- **Streaming reasoning durability.** qwen3-style `<think>` blocks: do we
  capture them in `Trace.reasoning` (we do today) and ship them to the hub?
  Useful for distillation, expensive in tokens. Currently the redactor passes
  them through; consider an opt-out.
- **Skill / agent loading scope.** Today `manifests_root` defaults to the
  ogcaibb repo root. A developer working in a bblock repo can't ship that
  repo's project-specific skills to the daemon without symlinking. Should
  the daemon also walk `$OGCAIBB_WORKSPACE_ROOT/.ogcaibb/skills/` (and
  similarly for agents and commands)?
