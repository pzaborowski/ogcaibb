"""Redactor Protocol.

Redaction runs **before** the WAL write, so the unredacted form never touches
disk or network. Implementations must be pure (no IO) and deterministic so
that tests are reliable.
"""

from __future__ import annotations

from typing import Protocol

from ..record import Trace


class Redactor(Protocol):
    name: str

    def redact(self, trace: Trace) -> Trace:
        """Return a redacted copy of `trace`. Never mutates in place."""
        ...
