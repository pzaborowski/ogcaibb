from __future__ import annotations

from typing import Any, Callable

from .base import VectorStore
from .memory import InMemoryVectorStore

_BUILDERS: dict[str, Callable[..., VectorStore]] = {}


def register(name: str, builder: Callable[..., VectorStore]) -> None:
    _BUILDERS[name] = builder


def get_vector_store(name: str, **kwargs: Any) -> VectorStore:
    if name not in _BUILDERS:
        raise ValueError(
            f"Unknown vector store: {name}. Registered: {sorted(_BUILDERS)}"
        )
    return _BUILDERS[name](**kwargs)


register("memory", lambda **_: InMemoryVectorStore())


def _build_qdrant(**kw: Any) -> VectorStore:
    # Lazy import so qdrant-client is only required when this backend is selected.
    from .qdrant import QdrantVectorStore

    return QdrantVectorStore(**kw)


register("qdrant", _build_qdrant)
