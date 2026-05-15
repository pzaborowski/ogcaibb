"""Single source of truth for the hub's HTTP surface.

Mirrors `ogcaibb.hub_client.endpoints` on the workstation side. The two files
are kept in sync deliberately — both packages ship independently, and a path
mismatch should fail loudly via a 404, not silently route somewhere unintended.
"""

from __future__ import annotations

HEALTH = "/healthz"
INGEST = "/v1/ingest"
FEEDBACK = "/v1/feedback"
RETRIEVE = "/v1/retrieve"
