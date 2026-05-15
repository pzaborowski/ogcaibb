from __future__ import annotations

from typing import Any, Callable

from .apikey import APIKeyAuth
from .base import HubAuth

_BUILDERS: dict[str, Callable[..., HubAuth]] = {}


def register(name: str, builder: Callable[..., HubAuth]) -> None:
    _BUILDERS[name] = builder


def get_hub_auth(name: str, **kwargs: Any) -> HubAuth:
    if name not in _BUILDERS:
        raise ValueError(f"Unknown hub auth: {name}. Registered: {sorted(_BUILDERS)}")
    return _BUILDERS[name](**kwargs)


register("apikey", lambda keys_file, **_: APIKeyAuth(keys_file=keys_file))
