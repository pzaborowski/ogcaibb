"""Name-keyed registry for trace transports."""

from __future__ import annotations

from typing import Any, Callable

from .base import TraceTransport
from .https import HTTPSTransport
from .noop import NoopTransport

_BUILDERS: dict[str, Callable[..., TraceTransport]] = {}


def register(name: str, builder: Callable[..., TraceTransport]) -> None:
    _BUILDERS[name] = builder


def get_transport(name: str, **kwargs: Any) -> TraceTransport:
    if name not in _BUILDERS:
        raise ValueError(f"Unknown transport: {name}. Registered: {sorted(_BUILDERS)}")
    return _BUILDERS[name](**kwargs)


register("noop", lambda **_: NoopTransport())
register("https", lambda **kw: HTTPSTransport(**kw))
