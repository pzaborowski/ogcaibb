"""Weak baseline detector: did the turn finish cleanly?

Fires +1 when the assistant produced output, no tool calls reported an error,
and the turn was not marked incomplete (e.g. by client disconnect). Useful
mostly as a sanity-check signal — easy to satisfy, low weight in aggregation.
"""

from __future__ import annotations

from ..record import Signal, utcnow
from .base import SignalContext


class ToolCallCompletedClean:
    name = "tool_call_completed_clean"
    delay_seconds = 0.0
    weight = 0.1

    async def detect(self, ctx: SignalContext) -> list[Signal]:
        if ctx.incomplete or ctx.error:
            return []
        if not ctx.assistant_text.strip() and not ctx.tool_calls:
            return []
        if _any_tool_return_errored(ctx):
            return []
        return [
            Signal(
                trace_id=ctx.trace_id,
                workstation_id=ctx.workstation_id,
                source=self.name,
                polarity=1,
                weight=self.weight,
                detected_at=utcnow(),
                meta={"tool_calls": len(ctx.tool_calls)},
            )
        ]


def _any_tool_return_errored(ctx: SignalContext) -> bool:
    for tc in ctx.tool_calls:
        if tc.kind != "ToolReturnPart":
            continue
        data = tc.data or {}
        content = data.get("content")
        if isinstance(content, str) and content.lower().startswith("error"):
            return True
        if data.get("is_error"):
            return True
    return False
