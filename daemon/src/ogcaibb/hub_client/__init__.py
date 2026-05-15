"""Workstation-side client for talking to ogcaibb-hub.

Authentication is pluggable (`auth/`). Today's only impl is API-key bearer;
GitHub OAuth slots in beside it by registering a new builder.
"""

from .auth import get_workstation_auth
from .client import HubClient

__all__ = ["HubClient", "get_workstation_auth"]
