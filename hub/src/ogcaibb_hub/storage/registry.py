from __future__ import annotations

from typing import Any, Callable

from .base import IndexStore, TraceStore
from .index_sqlite import SQLiteIndexStore
from .traces_localfs import LocalFSTraceStore

_TRACE_BUILDERS: dict[str, Callable[..., TraceStore]] = {}
_INDEX_BUILDERS: dict[str, Callable[..., IndexStore]] = {}


def register_trace_store(name: str, builder: Callable[..., TraceStore]) -> None:
    _TRACE_BUILDERS[name] = builder


def register_index_store(name: str, builder: Callable[..., IndexStore]) -> None:
    _INDEX_BUILDERS[name] = builder


def get_trace_store(name: str, **kwargs: Any) -> TraceStore:
    if name not in _TRACE_BUILDERS:
        raise ValueError(
            f"Unknown trace store: {name}. Registered: {sorted(_TRACE_BUILDERS)}"
        )
    return _TRACE_BUILDERS[name](**kwargs)


def get_index_store(name: str, **kwargs: Any) -> IndexStore:
    if name not in _INDEX_BUILDERS:
        raise ValueError(
            f"Unknown index store: {name}. Registered: {sorted(_INDEX_BUILDERS)}"
        )
    return _INDEX_BUILDERS[name](**kwargs)


register_trace_store("localfs", lambda root, **_: LocalFSTraceStore(root=root))
register_index_store("sqlite", lambda db_path, **_: SQLiteIndexStore(db_path=db_path))
