"""Name-keyed registry for workstation auth providers."""

from __future__ import annotations

from typing import Any, Callable

from .apikey import APIKeyAuth
from .base import WorkstationAuth

_BUILDERS: dict[str, Callable[..., WorkstationAuth]] = {}


def register(name: str, builder: Callable[..., WorkstationAuth]) -> None:
    _BUILDERS[name] = builder


def get_workstation_auth(name: str, **kwargs: Any) -> WorkstationAuth:
    if name not in _BUILDERS:
        raise ValueError(f"Unknown auth: {name}. Registered: {sorted(_BUILDERS)}")
    return _BUILDERS[name](**kwargs)


register("apikey", lambda token, **_: APIKeyAuth(token=token))
