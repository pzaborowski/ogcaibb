"""HTTP fetch with a small marine-vocabulary allowlist by default.

The allowlist isn't a security boundary — it's a guardrail to keep the model
from chasing arbitrary URLs during agent runs. Set OGCAIBB_WEB_ALLOW_ALL=1 to
disable. We deliberately do NOT add browser rendering / JS execution in v1.
"""

from __future__ import annotations

import logging
import os

import httpx

from .registry import register

log = logging.getLogger(__name__)

DEFAULT_ALLOWLIST = (
    "vocab.nerc.ac.uk",
    "vocab.ices.dk",
    "rs.tdwg.org",
    "www.marinespecies.org",
    "obis.org",
    "api.obis.org",
    "emodnet.ec.europa.eu",
    "helcom.fi",
    "cfconventions.org",
    "schema.org",
    "www.w3.org",
    "ogcincubator.github.io",
    "ogc.github.io",
    "raw.githubusercontent.com",
    "github.com",
)


def _allowed(url: str) -> bool:
    if os.environ.get("OGCAIBB_WEB_ALLOW_ALL") == "1":
        return True
    return any(domain in url for domain in DEFAULT_ALLOWLIST)


@register(
    "WebFetch",
    "GET a URL and return the response body (truncated at 200 KB). "
    "Only marine/vocabulary domains are reachable by default.",
)
async def web_fetch(url: str, max_bytes: int = 200_000) -> str:
    if not _allowed(url):
        raise PermissionError(
            f"URL {url} is not on the marine-vocabulary allowlist. "
            "Override with OGCAIBB_WEB_ALLOW_ALL=1 if intentional."
        )
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        body = r.content[:max_bytes]
        return body.decode("utf-8", errors="replace")
