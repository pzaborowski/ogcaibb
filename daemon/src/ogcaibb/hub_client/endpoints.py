"""Single source of truth for the hub's HTTP surface.

Workstation code constructs URLs as `f"{hub_url}{INGEST}"`. The hub's route
decorators consume the matching `endpoints.py` on its side. Keeping the
constants in code (not env-driven settings) is deliberate: the path is a
contract between client and server and must not drift at runtime.
"""

from __future__ import annotations

HEALTH = "/healthz"
INGEST = "/v1/ingest"
FEEDBACK = "/v1/feedback"
RETRIEVE = "/v1/retrieve"
