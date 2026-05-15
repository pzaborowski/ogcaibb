"""Bearer API-key auth.

The token is opaque to the workstation; the hub matches its sha256 against
its keys file. Rotation = issue a new token, paste the new sha256 into the
hub's keys file, revoke the old line.
"""

from __future__ import annotations


class APIKeyAuth:
    name = "apikey"

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("APIKeyAuth requires a non-empty token")
        self._token = token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}
