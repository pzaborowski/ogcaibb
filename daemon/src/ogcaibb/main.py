"""FastAPI app exposing an OpenAI-compatible /v1/chat/completions endpoint.

This is the surface that Continue.dev, Cline, aider, and our own CLI all talk to.
Internally it routes through the agent loop with the configured Ollama model.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__ as DAEMON_VERSION
from .agents.registry import AgentRegistry
from .commands.parser import parse_slash_command
from .config import settings
from .hub_client.auth import get_workstation_auth
from .loop import LoopResult, run_turn, run_turn_stream
from .ollama_client import OllamaUnavailable, get_client
from .skills.registry import SkillRegistry
from .tools import registry as tool_registry
from .tracing.record import Trace, ToolCallRecord, env_meta, load_workstation_id
from .tracing.redactor import get_redactor
from .tracing.transport import get_transport
from .tracing.uploader import Uploader
from .tracing.wal import WAL

log = logging.getLogger("ogcaibb")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level)
    log.info("ogcaibb starting; ollama_host=%s", settings.ollama_host)

    client = get_client()
    try:
        info = await client.health()
        log.info("Ollama reachable: %s", info)
    except OllamaUnavailable as e:
        log.error("FATAL: %s", e)
        # We do not exit — the /v1/* endpoints will refuse requests with 503
        # so the operator can see the error in their IDE plugin.

    app.state.skills = SkillRegistry.load(settings.manifests_root / "skills")
    app.state.agents = AgentRegistry.load(settings.manifests_root / "agents")
    app.state.commands_dir = settings.manifests_root / "commands"
    tool_registry.discover()
    log.info(
        "Loaded %d skills, %d agents, %d tools",
        len(app.state.skills),
        len(app.state.agents),
        len(tool_registry.all()),
    )

    # --- Trace capture pipeline ----------------------------------------
    app.state.workstation_id = None
    app.state.wal = None
    app.state.redactor = None
    app.state.uploader = None
    if settings.trace_enabled:
        try:
            app.state.workstation_id = load_workstation_id(settings.workstation_id_path)
            app.state.wal = WAL(
                settings.trace_dir,
                max_bytes=settings.trace_max_bytes,
                max_age_seconds=settings.trace_max_age_seconds,
                max_total_bytes=settings.trace_max_total_bytes,
            )
            app.state.redactor = get_redactor(
                settings.redactor,
                rules_path=settings.redactor_rules,
                workspace_root=settings.workspace_root,
            )
            transport_kwargs: dict[str, Any] = {}
            if settings.trace_transport == "https":
                if not settings.hub_url:
                    raise ValueError(
                        "OGCAIBB_HUB_URL must be set when OGCAIBB_TRACE_TRANSPORT=https"
                    )
                if not settings.hub_token:
                    raise ValueError(
                        "OGCAIBB_HUB_TOKEN must be set when OGCAIBB_HUB_AUTH=apikey"
                    )
                auth = get_workstation_auth(settings.hub_auth, token=settings.hub_token)
                transport_kwargs = {"hub_url": settings.hub_url, "auth": auth}
            transport = get_transport(settings.trace_transport, **transport_kwargs)
            app.state.uploader = Uploader(
                wal=app.state.wal,
                transport=transport,
                workstation_id=app.state.workstation_id,
                schema_version=1,
            )
            app.state.uploader.start()
            log.info(
                "trace capture enabled (dir=%s, transport=%s, workstation=%s)",
                settings.trace_dir, settings.trace_transport,
                app.state.workstation_id[:8],
            )
        except Exception as e:
            log.error("trace capture disabled due to setup error: %s", e)
            app.state.wal = None
            app.state.redactor = None
            app.state.uploader = None
    else:
        log.info("trace capture disabled (OGCAIBB_TRACE_ENABLED=0)")

    try:
        yield
    finally:
        if app.state.uploader is not None:
            await app.state.uploader.stop()
        if app.state.wal is not None:
            app.state.wal.close()
        await client.aclose()


app = FastAPI(title="ogcaibb", version="0.1.0", lifespan=lifespan)


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    # ogcaibb extension fields (clients can pass these; ignored by Continue.dev)
    agent: str | None = None
    skill: str | None = None


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    try:
        info = await get_client().health()
        return {"status": "ok", **info}
    except OllamaUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """Continue.dev calls this. Expose only the role-pinned model names."""
    now = int(time.time())
    models = [
        {"id": settings.model_chat, "object": "model", "created": now, "owned_by": "ogcaibb"},
        {"id": settings.model_router, "object": "model", "created": now, "owned_by": "ogcaibb"},
    ]
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    # Hard-fail if Ollama is gone — no silent fallback to anything.
    try:
        await get_client().health()
    except OllamaUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    messages = [m.model_dump(exclude_none=True) for m in req.messages]

    # Slash-command detection on the latest user message.
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    skill_name = req.skill
    agent_name = req.agent
    if last_user and isinstance(last_user.get("content"), str):
        parsed = parse_slash_command(last_user["content"], request.app.state.commands_dir)
        if parsed:
            skill_name = skill_name or parsed.get("skill")
            agent_name = agent_name or parsed.get("agent")
            last_user["content"] = parsed["expanded_prompt"]

    system_prompt = None
    allowed_tools: list[str] | None = None

    if agent_name:
        agent_def = request.app.state.agents.get(agent_name)
        if not agent_def:
            raise HTTPException(404, f"Unknown agent: {agent_name}")
        system_prompt = agent_def.system_prompt
        allowed_tools = agent_def.allowed_tools

    if skill_name:
        skill_def = request.app.state.skills.get(skill_name)
        if not skill_def:
            raise HTTPException(404, f"Unknown skill: {skill_name}")
        skill_intro = (
            f"Activated skill: {skill_def.name}\n"
            f"Skill description: {skill_def.description}\n"
            f"Skill instructions follow:\n\n{skill_def.body}"
        )
        system_prompt = (system_prompt + "\n\n" + skill_intro) if system_prompt else skill_intro

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model_name = req.model or settings.model_chat

    if req.stream:
        async def event_stream():
            role_sent = False
            tool_calls: list[dict[str, Any]] = []
            text_buf: list[str] = []
            reasoning_buf: list[str] = []
            stream_started = time.time()
            disconnected = False
            stream_error: str | None = None

            def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
                nonlocal role_sent
                if not role_sent:
                    delta = {"role": "assistant", **delta}
                    role_sent = True
                payload = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(payload)}\n\n"

            try:
                async for ev in run_turn_stream(
                    messages,
                    model=model_name,
                    system_prompt=system_prompt,
                    allowed_tools=allowed_tools,
                ):
                    if await request.is_disconnected():
                        log.info("client disconnected mid-stream; aborting")
                        disconnected = True
                        return
                    kind = ev["kind"]
                    if kind == "text":
                        text_buf.append(ev["delta"])
                        yield chunk({"content": ev["delta"]})
                    elif kind == "thinking":
                        reasoning_buf.append(ev["delta"])
                        yield chunk({"reasoning_content": ev["delta"]})
                    elif kind == "tool_call":
                        tc = {
                            "index": len(tool_calls),
                            "id": ev["id"],
                            "type": "function",
                            "function": {"name": ev["name"], "arguments": ev["args"]},
                        }
                        tool_calls.append(tc)
                        yield chunk({"tool_calls": [tc]})
                    elif kind == "tool_return":
                        yield chunk(
                            {"ogcaibb_tool_return": {
                                "id": ev["id"], "name": ev["name"],
                                "content": ev["content"][:2000],
                            }}
                        )
                    elif kind == "done":
                        yield chunk({}, finish="stop")
                        yield "data: [DONE]\n\n"
            except OllamaUnavailable as e:
                stream_error = str(e)
                err = {"error": {"message": str(e), "type": "server_error"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                synthetic = LoopResult(
                    text="".join(text_buf),
                    tool_calls=[{"kind": "ToolCallPart", "data": tc} for tc in tool_calls],
                    model=model_name,
                    reasoning="".join(reasoning_buf),
                    started_at=stream_started,
                    ended_at=time.time(),
                    error=stream_error,
                    incomplete=disconnected or stream_error is not None,
                )
                _capture_trace(
                    request.app,
                    messages_in=messages,
                    result=synthetic,
                    agent_name=agent_name,
                    skill_name=skill_name,
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Non-streaming path
    try:
        result = await run_turn(
            messages,
            model=model_name,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
        )
    except OllamaUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    _capture_trace(
        request.app,
        messages_in=messages,
        result=result,
        agent_name=agent_name,
        skill_name=skill_name,
    )

    message: dict[str, Any] = {"role": "assistant", "content": result.text}
    if result.reasoning:
        message["reasoning_content"] = result.reasoning

    completion = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": result.model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "ogcaibb": {"tool_calls": result.tool_calls, "reasoning": result.reasoning},
    }
    return JSONResponse(completion)


def _capture_trace(
    app: FastAPI,
    *,
    messages_in: list[dict[str, Any]],
    result: LoopResult,
    agent_name: str | None,
    skill_name: str | None,
) -> None:
    """Redact and persist a completed turn. Best-effort: never raises."""
    wal = getattr(app.state, "wal", None)
    redactor = getattr(app.state, "redactor", None)
    workstation_id = getattr(app.state, "workstation_id", None)
    if wal is None or redactor is None or not workstation_id:
        return
    try:
        trace = Trace(
            workstation_id=workstation_id,
            daemon_version=DAEMON_VERSION,
            started_at=datetime.fromtimestamp(result.started_at or time.time(), tz=timezone.utc),
            ended_at=datetime.fromtimestamp(result.ended_at or time.time(), tz=timezone.utc),
            agent=agent_name,
            skill=skill_name,
            model=result.model,
            messages_in=messages_in,
            tool_calls=[ToolCallRecord(**tc) for tc in result.tool_calls],
            assistant_text=result.text,
            reasoning=result.reasoning or None,
            error=result.error,
            incomplete=result.incomplete,
            meta=env_meta(),
        )
        wal.append_trace(redactor.redact(trace))
    except Exception as e:
        log.warning("trace capture failed: %s", e)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "ogcaibb.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    run()
