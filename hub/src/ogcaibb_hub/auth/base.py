"""HubAuth Protocol — verifies an incoming request and returns an Identity.

Implementations should raise fastapi.HTTPException(401|403) on failure so the
middleware can propagate the right status code without each route knowing
about specific provider semantics.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from pydantic import BaseModel, Field


class Identity(BaseModel):
    sub: str               # stable unique id, e.g. "apikey:piotr-laptop"
    kind: str              # "apikey" | "github" | ...
    display_name: str = ""
    attrs: dict[str, Any] = Field(default_factory=dict)


class HubAuth(Protocol):
    name: str

    async def verify(self, headers: Mapping[str, str]) -> Identity: ...
