"""HTTPS transport: POST gz JSONL chunks to the hub's /v1/ingest."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ...hub_client.auth.base import WorkstationAuth
from .base import Ack, ChunkManifest, TransportError

log = logging.getLogger(__name__)


class HTTPSTransport:
    name = "https"

    def __init__(
        self,
        *,
        hub_url: str,
        auth: WorkstationAuth,
        timeout: float = 30.0,
    ) -> None:
        if not hub_url:
            raise ValueError("hub_url is required for HTTPSTransport")
        self._hub_url = hub_url.rstrip("/")
        self._auth = auth
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, chunk_path: Path, manifest: ChunkManifest) -> Ack:
        body = chunk_path.read_bytes()
        headers = {
            "Content-Type": "application/x-ogcaibb-trace-chunk",
            "Content-Encoding": "gzip",
            "X-Schema-Version": str(manifest.schema_version),
            "X-Workstation-Id": manifest.workstation_id,
            "X-Chunk-Sequence": str(manifest.sequence),
            "X-Chunk-Sha256": manifest.sha256,
            "X-Chunk-Records": str(manifest.record_count),
        }
        headers.update(self._auth.headers())

        url = f"{self._hub_url}/v1/ingest"
        try:
            resp = await self._client.post(url, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise TransportError(f"network error talking to {url}: {e}", retriable=True) from e

        if resp.status_code in (401, 403):
            raise TransportError(
                f"auth rejected by hub: {resp.status_code} {resp.text[:200]}",
                retriable=False,
            )
        if resp.status_code == 415:
            raise TransportError(
                f"hub rejected schema_version={manifest.schema_version}",
                retriable=False,
            )
        if resp.status_code >= 500:
            raise TransportError(
                f"hub server error: {resp.status_code} {resp.text[:200]}",
                retriable=True,
            )
        if resp.status_code >= 400:
            raise TransportError(
                f"hub rejected chunk: {resp.status_code} {resp.text[:200]}",
                retriable=False,
            )

        try:
            data = resp.json()
        except Exception:
            data = {}
        return Ack(
            accepted=int(data.get("accepted", manifest.record_count)),
            duplicates=int(data.get("duplicates", 0)),
            errors=list(data.get("errors") or []),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
