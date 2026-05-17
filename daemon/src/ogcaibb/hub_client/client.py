"""Thin async client for the hub's REST surface.

Owns one httpx.AsyncClient per HubClient instance; transport reuses the same
client for chunk uploads when constructed with `from_hub_client()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from . import endpoints
from .auth.base import WorkstationAuth

log = logging.getLogger(__name__)


@dataclass
class Exemplar:
    trace_id: str
    score: float
    user_message: str
    assistant_text: str
    agent: str | None
    skill: str | None
    model: str


class HubClient:
    def __init__(
        self,
        *,
        hub_url: str,
        auth: WorkstationAuth,
        timeout: float = 15.0,
    ) -> None:
        if not hub_url:
            raise ValueError("hub_url is required")
        self._hub_url = hub_url.rstrip("/")
        self._auth = auth
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def hub_url(self) -> str:
        return self._hub_url

    @property
    def auth(self) -> WorkstationAuth:
        return self._auth

    async def health(self) -> dict:
        resp = await self._client.get(
            f"{self._hub_url}{endpoints.HEALTH}",
            headers=self._auth.headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
        agent: str | None = None,
        skill: str | None = None,
        scope: str = "all",
    ) -> list[Exemplar]:
        """Fetch top-k exemplars semantically similar to `query`.

        Returns an empty list on transport/auth/server errors — retrieval is
        best-effort; the caller proceeds with the model turn either way.
        """
        if not query.strip():
            return []
        body: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "min_score": min_score,
            "filter": {"agent": agent, "skill": skill, "scope": scope},
        }
        try:
            resp = await self._client.post(
                f"{self._hub_url}{endpoints.RETRIEVE}",
                json=body,
                headers=self._auth.headers(),
            )
        except httpx.HTTPError as e:
            log.warning("retrieve: network error: %s", e)
            return []
        if resp.status_code >= 400:
            log.warning(
                "retrieve: hub returned %d: %s", resp.status_code, resp.text[:200]
            )
            return []
        data = resp.json() or {}
        out: list[Exemplar] = []
        for r in data.get("results") or []:
            try:
                out.append(
                    Exemplar(
                        trace_id=str(r["trace_id"]),
                        score=float(r.get("score", 0.0)),
                        user_message=str(r.get("user_message", "")),
                        assistant_text=str(r.get("assistant_text", "")),
                        agent=r.get("agent"),
                        skill=r.get("skill"),
                        model=str(r.get("model", "")),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                log.debug("retrieve: skipping malformed result: %s", e)
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
