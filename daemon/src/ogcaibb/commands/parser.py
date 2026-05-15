"""Slash-command parsing.

When a user message starts with /<command>, we look up commands/<command>.md
and use the file's body as an expanded prompt template. Trivial $ARGUMENTS
substitution is supported (matches Claude Code's behaviour).

Frontmatter fields we honour:
  - argument-hint  (informational only)
  - description    (informational only)
  - skill          (auto-activate that skill)
  - agent          (route through that agent)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frontmatter

log = logging.getLogger(__name__)


def parse_slash_command(message: str, commands_dir: Path) -> dict[str, Any] | None:
    text = message.lstrip()
    if not text.startswith("/"):
        return None

    head, _, rest = text.partition(" ")
    name = head[1:].strip()
    args = rest.strip()
    if not name:
        return None

    path = commands_dir / f"{name}.md"
    if not path.exists():
        return None

    try:
        post = frontmatter.load(path)
    except Exception as e:
        log.error("Failed to parse command %s: %s", path, e)
        return None

    body = post.content.strip()
    expanded = body.replace("$ARGUMENTS", args)
    return {
        "command": name,
        "args": args,
        "expanded_prompt": expanded,
        "skill": post.metadata.get("skill"),
        "agent": post.metadata.get("agent"),
        "description": post.metadata.get("description"),
    }
