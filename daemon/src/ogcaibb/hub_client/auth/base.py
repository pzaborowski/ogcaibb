"""WorkstationAuth Protocol.

Implementations return the headers to attach to outgoing hub requests. They
may be stateful (e.g. cached OAuth access token with a refresh routine) but
must remain cheap to call — `headers()` is invoked on every outbound request.
"""

from __future__ import annotations

from typing import Protocol


class WorkstationAuth(Protocol):
    name: str

    def headers(self) -> dict[str, str]:
        """Return headers to merge into outgoing hub requests."""
        ...
