"""Name-keyed registry for implicit signal detectors."""

from __future__ import annotations

from typing import Any, Callable

from .base import ImplicitSignal
from .edit_retention import EditRetention
from .git_commit import GitCommit
from .tool_call_completed_clean import ToolCallCompletedClean

_BUILDERS: dict[str, Callable[..., ImplicitSignal]] = {}


def register(name: str, builder: Callable[..., ImplicitSignal]) -> None:
    _BUILDERS[name] = builder


def get_signal(name: str, **kwargs: Any) -> ImplicitSignal:
    if name not in _BUILDERS:
        raise ValueError(
            f"Unknown implicit signal: {name}. Registered: {sorted(_BUILDERS)}"
        )
    return _BUILDERS[name](**kwargs)


def all_signals() -> list[str]:
    return sorted(_BUILDERS)


register("tool_call_completed_clean", lambda **_: ToolCallCompletedClean())
register("edit_retention", lambda **kw: EditRetention(**kw))
register("git_commit", lambda **kw: GitCommit(**kw))
