"""API-key auth: match Bearer-token sha256 against a YAML keys file.

The keys file is a single document of shape:

    keys:
      - name: piotr-laptop            # display name
        sub: apikey:piotr-laptop      # stable identity (free-form, must be unique)
        key_sha256: 5e8a...           # sha256(hex) of the bearer token
        # optional:
        attrs: { team: marine }
        revoked: false

`name` and `sub` are admin-supplied. Tokens themselves are never stored.
Reloading: the file is re-read on every verify() call (cheap; <100 entries).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Mapping

import yaml
from fastapi import HTTPException

from .base import Identity

log = logging.getLogger(__name__)

_RELOAD_INTERVAL = 5.0  # seconds


class APIKeyAuth:
    name = "apikey"

    def __init__(self, keys_file: Path) -> None:
        self._keys_file = keys_file.expanduser()
        self._mtime: float = 0.0
        self._cache_at: float = 0.0
        self._by_hash: dict[str, dict] = {}

    async def verify(self, headers: Mapping[str, str]) -> Identity:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        token = auth.split(None, 1)[1].strip()
        if not token:
            raise HTTPException(401, "empty bearer token")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = self._lookup(digest)
        if record is None:
            raise HTTPException(401, "unknown api key")
        if record.get("revoked"):
            raise HTTPException(403, "api key revoked")
        return Identity(
            sub=str(record.get("sub") or f"apikey:{record.get('name','unknown')}"),
            kind="apikey",
            display_name=str(record.get("name", "")),
            attrs=dict(record.get("attrs") or {}),
        )

    def _lookup(self, digest: str) -> dict | None:
        self._refresh_if_stale()
        return self._by_hash.get(digest)

    def _refresh_if_stale(self) -> None:
        now = time.time()
        if now - self._cache_at < _RELOAD_INTERVAL and self._by_hash:
            return
        try:
            stat = self._keys_file.stat()
        except FileNotFoundError:
            if self._by_hash:
                log.warning("keys file %s disappeared; keeping last cached set", self._keys_file)
            self._cache_at = now
            return
        if stat.st_mtime == self._mtime and self._by_hash:
            self._cache_at = now
            return
        try:
            data = yaml.safe_load(self._keys_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.error("failed to parse keys file %s: %s", self._keys_file, e)
            self._cache_at = now
            return
        new_map: dict[str, dict] = {}
        for entry in data.get("keys") or []:
            digest = (entry.get("key_sha256") or "").lower().strip()
            if not digest:
                continue
            new_map[digest] = entry
        self._by_hash = new_map
        self._mtime = stat.st_mtime
        self._cache_at = now
        log.info("loaded %d api keys from %s", len(self._by_hash), self._keys_file)
