"""Thin async client for the hub's REST surface.

Owns one httpx.AsyncClient per HubClient instance; transport reuses the same
client for chunk uploads when constructed with `from_hub_client()`.
"""

from __future__ import annotations

import logging

import httpx

from . import endpoints
from .auth.base import WorkstationAuth

log = logging.getLogger(__name__)


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

    async def aclose(self) -> None:
        await self._client.aclose()
