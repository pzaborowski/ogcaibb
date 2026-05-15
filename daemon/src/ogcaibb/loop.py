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
from typing import Any

from pydantic_ai import Agent
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
) -> LoopResult:
    if not messages:
        raise ValueError("messages must be non-empty")

    model_name = model or settings.model_chat
    sys_prompt = system_prompt or _default_system_prompt()

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


def _default_system_prompt() -> str:
    return (
        "You are ogcaibb, a local AI assistant for OGC Building Block repositories.\n"
        f"Your workspace root is {settings.workspace_root}. "
        "All file paths you create or edit must live inside this root.\n\n"
        "CRITICAL RULES:\n"
        "1. When the user asks you to create, write, or update files, you MUST "
        "call the Write tool for each file. Do NOT paste file contents into "
        "your reply as code blocks instead of writing them.\n"
        "2. When the user asks you to edit existing files, use the Edit tool. "
        "Use Read first if you need to see the current content.\n"
        "3. Before writing, briefly state which files you are about to create "
        "and their absolute paths. After all writes succeed, summarise what "
        "you wrote — but never duplicate the file contents in your reply.\n"
        "4. Never invent vocabulary URIs — call the WebFetch tool to look them "
        "up on the marine-vocabulary allowlist (NERC, CF, Darwin Core, OBIS, ICES).\n"
        "5. If a Write call fails with a permission error, the path is outside "
        "the workspace — report this to the user, do not paste contents.\n"
    )


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
    sys_prompt = system_prompt or _default_system_prompt()
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
