"""Loads agent manifests from disk.

An agent is a markdown file (any .md under agents/) with YAML frontmatter:

    ---
    name: validation-agent
    description: ...
    tools: Read, Bash, Grep, Glob
    model: qwen2.5-coder:32b-instruct
    ---

    <body becomes the system prompt>

Same format as Claude Code subagents so the repo is dual-usable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

log = logging.getLogger(__name__)


@dataclass
class AgentDef:
    name: str
    description: str
    system_prompt: str
    path: Path
    allowed_tools: list[str] | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    def __init__(self, agents: dict[str, AgentDef]) -> None:
        self._agents = agents

    def __len__(self) -> int:
        return len(self._agents)

    def __iter__(self):
        return iter(self._agents.values())

    def get(self, name: str) -> AgentDef | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return sorted(self._agents)

    @classmethod
    def load(cls, root: Path) -> "AgentRegistry":
        agents: dict[str, AgentDef] = {}
        if not root.exists():
            log.warning("Agents root %s does not exist", root)
            return cls(agents)

        for md in root.rglob("*.md"):
            if md.name.upper() == "README.MD":
                continue
            try:
                post = frontmatter.load(md)
            except Exception as e:
                log.error("Failed to parse %s: %s", md, e)
                continue
            meta = dict(post.metadata)
            if not meta.get("name"):
                continue  # not an agent manifest
            name = meta["name"]
            tools = meta.get("tools")
            if isinstance(tools, str):
                tools = [t.strip() for t in tools.split(",") if t.strip()]
            agent = AgentDef(
                name=name,
                description=meta.get("description", ""),
                system_prompt=post.content.strip(),
                path=md,
                allowed_tools=tools,
                model=meta.get("model"),
                metadata=meta,
            )
            if name in agents:
                log.warning("Duplicate agent name %s (overwriting %s)", name, agents[name].path)
            agents[name] = agent
        log.info("Loaded %d agents from %s", len(agents), root)
        return cls(agents)
