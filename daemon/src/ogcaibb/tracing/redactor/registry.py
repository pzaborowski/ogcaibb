"""Name-keyed registry for Redactor implementations.

To add a new redactor, register it here and select it via `OGCAIBB_REDACTOR`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .base import Redactor
from .ruleset import RuleSetRedactor, RuleSetRedactorConfig

log = logging.getLogger(__name__)

_BUILDERS: dict[str, Callable[..., Redactor]] = {}


def register(name: str, builder: Callable[..., Redactor]) -> None:
    _BUILDERS[name] = builder


def get_redactor(
    name: str,
    *,
    rules_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Redactor:
    if name not in _BUILDERS:
        raise ValueError(f"Unknown redactor: {name}. Registered: {sorted(_BUILDERS)}")
    return _BUILDERS[name](rules_path=rules_path, workspace_root=workspace_root)


def _build_ruleset(
    *,
    rules_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Redactor:
    if rules_path and rules_path.exists():
        cfg = RuleSetRedactorConfig.load_yaml(rules_path)
    else:
        if rules_path:
            log.info("Redactor rules file %s not found; using defaults", rules_path)
        cfg = RuleSetRedactorConfig()
    if workspace_root is not None:
        cfg.workspace_root = workspace_root
    return RuleSetRedactor(cfg)


register("ruleset", _build_ruleset)
