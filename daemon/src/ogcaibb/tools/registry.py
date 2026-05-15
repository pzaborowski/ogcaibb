"""In-process tool registry.

Tools are Python callables registered via `@register(name, description)`.
The registry hands PydanticAI a list of Tool objects, filtered by the
agent's allowlist if one is set. There is no MCP layer in v1 — adding MCP
later would mean wrapping these same functions in an MCP server.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Tool

log = logging.getLogger(__name__)


@dataclass
class ToolDef:
    name: str
    description: str
    func: Callable[..., Any]


_TOOLS: dict[str, ToolDef] = {}


def register(name: str, description: str):
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _TOOLS:
            log.warning("Tool %s re-registered (overwriting)", name)
        _TOOLS[name] = ToolDef(name=name, description=description, func=func)
        return func

    return deco


def discover() -> None:
    """Import every sibling module so @register decorators run."""
    import ogcaibb.tools as pkg

    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in {"registry", "__init__"}:
            continue
        importlib.import_module(f"ogcaibb.tools.{mod.name}")


def all() -> dict[str, ToolDef]:
    return dict(_TOOLS)


def resolve(allowed: list[str] | None) -> list[Tool]:
    """Return PydanticAI Tool wrappers, optionally filtered."""
    selected = _TOOLS.values() if allowed is None else (
        t for n, t in _TOOLS.items() if n in set(allowed)
    )
    return [Tool(t.func, name=t.name, description=t.description) for t in selected]
