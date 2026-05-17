"""Agent loop. Wraps PydanticAI with an Ollama-backed model and the tool registry.

The loop exposes a single async entrypoint, `run_turn`, that takes a list of
chat messages (OpenAI shape) and returns the assistant's final message plus
any tool-call trace. Skill/agent selection happens upstream — by the time we
reach this loop the system prompt, allowed tools, and model are decided.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter
from pydantic_ai import Agent

if TYPE_CHECKING:
    from .agents.registry import AgentRegistry
    from .skills.registry import SkillRegistry
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import settings
from .tools import registry as tool_registry

log = logging.getLogger(__name__)


@dataclass
class LoopResult:
    text: str
    tool_calls: list[dict[str, Any]]
    model: str
    reasoning: str = ""
    started_at: float = 0.0  # monotonic-friendly unix ts
    ended_at: float = 0.0
    error: str | None = None
    incomplete: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _extract_reasoning(messages) -> str:
    """Pull ThinkingPart content out of PydanticAI's message trace.

    Ollama's OpenAI-compat endpoint returns reasoning models' chain of thought
    in a `reasoning` field separate from `content`. PydanticAI exposes those
    chunks as `ThinkingPart` objects with a `content` attribute. We concatenate
    them in order.
    """
    chunks: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if type(part).__name__ == "ThinkingPart":
                content = getattr(part, "content", None)
                if content:
                    chunks.append(content)
    return "\n\n".join(chunks).strip()


def _build_model(model_name: str) -> OpenAIModel:
    """Use Ollama's OpenAI-compatible endpoint via PydanticAI's OpenAI provider."""
    base_url = settings.ollama_host.rstrip("/") + "/v1"
    provider = OpenAIProvider(
        base_url=base_url,
        api_key=settings.ollama_api_key or "ollama",
    )
    return OpenAIModel(model_name, provider=provider)


def _build_agent(
    model_name: str,
    system_prompt: str,
    allowed_tools: list[str] | None,
) -> Agent:
    model = _build_model(model_name)
    tools = tool_registry.resolve(allowed_tools)
    return Agent(model=model, system_prompt=system_prompt, tools=tools)


async def run_turn(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    menu: str | None = None,
    exemplars: str | None = None,
) -> LoopResult:
    if not messages:
        raise ValueError("messages must be non-empty")

    model_name = model or settings.model_chat
    sys_prompt = _resolve_system_prompt(system_prompt, menu=menu, exemplars=exemplars)

    user_text = messages[-1].get("content", "")
    history = messages[:-1]

    agent = _build_agent(model_name, sys_prompt, allowed_tools)
    log.debug("loop.run_turn model=%s tools=%s", model_name, allowed_tools)

    started_at = time.time()
    try:
        result = await agent.run(user_text, message_history=_to_pydanticai_history(history))
    except Exception as e:
        return LoopResult(
            text="",
            tool_calls=[],
            model=model_name,
            started_at=started_at,
            ended_at=time.time(),
            error=str(e),
            incomplete=True,
        )

    trace: list[dict[str, Any]] = []
    for msg in result.new_messages():
        for part in getattr(msg, "parts", []):
            kind = type(part).__name__
            if kind in {"ToolCallPart", "ToolReturnPart"}:
                trace.append({"kind": kind, "data": _safe_dump(part)})

    reasoning = _extract_reasoning(result.new_messages())
    return LoopResult(
        text=result.output or "",
        tool_calls=trace,
        model=model_name,
        reasoning=reasoning,
        started_at=started_at,
        ended_at=time.time(),
    )


_CRITICAL_RULES_MARKER = "CRITICAL RULES (apply to every turn regardless of skill or agent):"
_MENU_MARKER = "## Available skills, agents, and slash commands"
_EXEMPLARS_MARKER = "## Relevant past exemplars (from the team's accepted prompts)"
_DEFAULT_INTRO = "You are ogcaibb, a local AI assistant for OGC Building Block repositories."
_MENU_DESC_MAXLEN = 220
_EXEMPLAR_SNIPPET_MAXLEN = 600


def _critical_rules() -> str:
    """Invariants every turn must enforce.

    These are appended to *every* system prompt — default, skill-augmented, or
    agent-supplied — so that tool-use semantics don't silently drop when a
    custom agent prompt takes over. The previous failure mode was small models
    (e.g. qwen3:4b) running a single Write and pasting the rest of the files
    as markdown code blocks; rule 3 below explicitly addresses that.
    """
    return (
        f"{_CRITICAL_RULES_MARKER}\n"
        f"1. Your workspace root is {settings.workspace_root}. All file paths "
        "you create or edit must live inside this root.\n"
        "2. When asked to create or update files, you MUST call the Write tool "
        "for EACH file. Do NOT paste file contents into your reply as code "
        "blocks — code blocks are never persisted to disk.\n"
        "3. Before producing your final answer, enumerate every file the user "
        "expects (or that your plan promised) and verify each has a successful "
        "Write call in the conversation. If any is missing, make the Write "
        "call now; do not finish the turn with files only described in prose.\n"
        "4. Use Edit (preceded by Read when you need current content) to "
        "modify existing files. Do not Write over an existing file unless the "
        "user explicitly asked for a full rewrite.\n"
        "5. Never invent vocabulary URIs — call the WebFetch tool to look them "
        "up on the marine-vocabulary allowlist (NERC, CF, Darwin Core, OBIS, "
        "ICES).\n"
        "6. If a Write call fails with a permission error, the path is outside "
        "the workspace — report this to the user, do not paste contents.\n"
    )


def _resolve_system_prompt(
    custom: str | None,
    *,
    menu: str | None = None,
    exemplars: str | None = None,
) -> str:
    """Compose the final system prompt.

    Order is deliberate:
      1. Caller-supplied prompt (agent + skill body, or the default intro)
      2. Menu of available skills/agents/commands so the model knows what's
         on offer in this workspace
      3. Relevant past exemplars retrieved from the hub
      4. CRITICAL rules — last because most chat models weight the most-recent
         system content highest.

    Idempotent on each marker — if `custom` already embeds one of them (e.g.
    a manual override), we don't re-append it.
    """
    parts: list[str] = []
    if custom is None:
        parts.append(_DEFAULT_INTRO)
    else:
        parts.append(custom.rstrip())

    if menu and _MENU_MARKER not in (custom or ""):
        parts.append(menu.rstrip())
    if exemplars and _EXEMPLARS_MARKER not in (custom or ""):
        parts.append(exemplars.rstrip())

    rendered = "\n\n".join(parts)
    if _CRITICAL_RULES_MARKER in rendered:
        return rendered
    return rendered + "\n\n" + _critical_rules()


def _default_system_prompt() -> str:
    """Backward-compat: the default system prompt with no menu."""
    return _resolve_system_prompt(None)


def _build_menu(
    skills: "SkillRegistry | None" = None,
    agents: "AgentRegistry | None" = None,
    commands_dir: Path | None = None,
) -> str | None:
    """Render a markdown manifest of locally available skills/agents/commands.

    Returned text is suitable for direct injection into the system prompt;
    returns None when there is nothing to advertise. Descriptions are taken
    from each item's frontmatter when present, truncated for budget.

    Skills/agents are not auto-activated by mentioning them here — the model
    still needs to either request activation (future skills-as-tools work) or
    instruct the user to invoke a slash command. The menu's job is purely
    discoverability so the model stops behaving as if these don't exist.
    """
    sections: list[str] = []

    if skills is not None and len(skills) > 0:
        rows = [
            f"- **{s.name}** — {_truncate(s.description)}"
            for s in sorted(skills, key=lambda s: s.name)
            if s.description
        ]
        if rows:
            sections.append("### Skills\n" + "\n".join(rows))

    if agents is not None and len(agents) > 0:
        rows = [
            f"- **{a.name}** — {_truncate(a.description)}"
            for a in sorted(agents, key=lambda a: a.name)
            if a.description
        ]
        if rows:
            sections.append("### Agents\n" + "\n".join(rows))

    if commands_dir is not None and commands_dir.exists():
        rows: list[str] = []
        for md in sorted(commands_dir.glob("*.md")):
            slash = md.stem
            desc = _command_description(md)
            if desc:
                rows.append(f"- `/{slash}` — {_truncate(desc)}")
            else:
                rows.append(f"- `/{slash}`")
        if rows:
            sections.append("### Slash commands\n" + "\n".join(rows))

    if not sections:
        return None

    header = (
        f"{_MENU_MARKER}\n"
        "These are loaded locally in this workspace. Prefer activating a "
        "matching skill or invoking the appropriate slash command over "
        "improvising — the wrapped logic enforces project conventions the "
        "default prompt can't fully express."
    )
    return header + "\n\n" + "\n\n".join(sections)


def format_exemplars(items: list[dict[str, Any]]) -> str | None:
    """Render hub-retrieved exemplars as a system-prompt section.

    `items` is the shape returned by `HubClient.retrieve`: each entry has
    user_message, assistant_text, agent, skill, model, score. Returns None
    when there's nothing useful to inject.
    """
    rows: list[str] = []
    for i, ex in enumerate(items, 1):
        user = (ex.get("user_message") or "").strip()
        assistant = (ex.get("assistant_text") or "").strip()
        if not user and not assistant:
            continue
        meta_bits = []
        if ex.get("agent"):
            meta_bits.append(f"agent={ex['agent']}")
        if ex.get("skill"):
            meta_bits.append(f"skill={ex['skill']}")
        score = ex.get("score")
        if isinstance(score, (int, float)):
            meta_bits.append(f"score={score:.2f}")
        header = f"### {i}. " + (" · ".join(meta_bits) or "exemplar")
        rows.append(
            f"{header}\n"
            f"_User_: {_truncate(user, _EXEMPLAR_SNIPPET_MAXLEN)}\n"
            f"_Assistant_: {_truncate(assistant, _EXEMPLAR_SNIPPET_MAXLEN)}"
        )
    if not rows:
        return None
    intro = (
        f"{_EXEMPLARS_MARKER}\n"
        "Drawn from past turns that were either explicitly upvoted or that "
        "scored positive on implicit signals. Use them as guidance for "
        "approach, tone, and prior decisions — not as ground truth, and "
        "not as instructions to copy verbatim."
    )
    return intro + "\n\n" + "\n\n".join(rows)


def _truncate(text: str, limit: int = _MENU_DESC_MAXLEN) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _command_description(path: Path) -> str:
    """Pull a one-line description for a slash-command markdown file."""
    try:
        post = frontmatter.load(path)
    except Exception:
        return ""
    desc = post.metadata.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    # Fall back to the first non-empty line of the body.
    for line in post.content.splitlines():
        if line.strip():
            # Strip leading markdown emphasis / headings.
            return line.strip().lstrip("#").strip().strip("*_`").strip()
    return ""


def _to_pydanticai_history(history: list[dict[str, Any]]):
    # Placeholder: PydanticAI accepts None and rebuilds the chain from `user_text`.
    # When we wire streaming/branching we'll convert OpenAI messages to ModelMessage parts.
    return None if not history else None


def _safe_dump(obj: Any) -> dict[str, Any]:
    try:
        return obj.model_dump()  # pydantic v2
    except AttributeError:
        return {"repr": repr(obj)}


async def run_turn_stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    menu: str | None = None,
    exemplars: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield typed events for true token-by-token streaming.

    Event shapes (all dicts):
      {"kind": "text",         "delta": str}
      {"kind": "thinking",     "delta": str}
      {"kind": "tool_call",    "id": str, "name": str, "args": str}
      {"kind": "tool_return",  "id": str, "name": str, "content": str}
      {"kind": "done",         "model": str}

    Tool-call arguments are emitted only once the model has finished assembling
    them (PydanticAI's FunctionToolCallEvent), not as partial JSON deltas — the
    OpenAI streaming spec allows either but Continue.dev handles "once" better.
    """
    if not messages:
        raise ValueError("messages must be non-empty")

    model_name = model or settings.model_chat
    sys_prompt = _resolve_system_prompt(system_prompt, menu=menu, exemplars=exemplars)
    user_text = messages[-1].get("content", "")

    agent = _build_agent(model_name, sys_prompt, allowed_tools)
    log.debug("loop.run_turn_stream model=%s tools=%s", model_name, allowed_tools)

    # Track which part-index belongs to which kind, since PartDeltaEvent only
    # carries the index — we need PartStartEvent to learn the kind.
    index_kind: dict[int, str] = {}

    async with agent.iter(user_text) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as request_stream:
                    async for event in request_stream:
                        if isinstance(event, PartStartEvent):
                            part = event.part
                            if isinstance(part, TextPart):
                                index_kind[event.index] = "text"
                                if part.content:
                                    yield {"kind": "text", "delta": part.content}
                            elif isinstance(part, ThinkingPart):
                                index_kind[event.index] = "thinking"
                                if part.content:
                                    yield {"kind": "thinking", "delta": part.content}
                            elif isinstance(part, ToolCallPart):
                                index_kind[event.index] = "tool_call"
                                # full args come later via FunctionToolCallEvent
                        elif isinstance(event, PartDeltaEvent):
                            kind = index_kind.get(event.index)
                            delta = event.delta
                            if isinstance(delta, TextPartDelta) and delta.content_delta:
                                yield {"kind": "text", "delta": delta.content_delta}
                            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                                yield {"kind": "thinking", "delta": delta.content_delta}
                            # ToolCallPartDelta arg-fragments are ignored; we
                            # emit a single tool_call event on completion below.
                            _ = kind  # reserved for future routing
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as tool_stream:
                    async for event in tool_stream:
                        if isinstance(event, FunctionToolCallEvent):
                            p = event.part
                            yield {
                                "kind": "tool_call",
                                "id": p.tool_call_id,
                                "name": p.tool_name,
                                "args": p.args if isinstance(p.args, str) else str(p.args),
                            }
                        elif isinstance(event, FunctionToolResultEvent):
                            res = event.result
                            yield {
                                "kind": "tool_return",
                                "id": getattr(res, "tool_call_id", ""),
                                "name": getattr(res, "tool_name", ""),
                                "content": str(getattr(res, "content", "")),
                            }

    yield {"kind": "done", "model": model_name}
