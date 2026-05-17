from __future__ import annotations

from typing import Any, Callable

from .base import Embedder
from .ollama import OllamaEmbedder

_BUILDERS: dict[str, Callable[..., Embedder]] = {}


def register(name: str, builder: Callable[..., Embedder]) -> None:
    _BUILDERS[name] = builder


def get_embedder(name: str, **kwargs: Any) -> Embedder:
    if name not in _BUILDERS:
        raise ValueError(
            f"Unknown embedder: {name}. Registered: {sorted(_BUILDERS)}"
        )
    return _BUILDERS[name](**kwargs)


register("ollama", lambda **kw: OllamaEmbedder(**kw))
