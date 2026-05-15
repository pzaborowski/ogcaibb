"""Loads skill manifests from disk.

A skill is a markdown file (typically SKILL.md inside a directory) with YAML
frontmatter declaring at minimum a name and description. The body is the
prompt the model sees once the skill is activated.

Format is identical to Claude Code skills so this repo's contents are
dual-usable: ogcaibb daemon + Claude Code in any bblock workspace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

log = logging.getLogger(__name__)


@dataclass
class SkillDef:
    name: str
    description: str
    body: str
    path: Path
    allowed_tools: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillRegistry:
    def __init__(self, skills: dict[str, SkillDef]) -> None:
        self._skills = skills

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self._skills.values())

    def get(self, name: str) -> SkillDef | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    @classmethod
    def load(cls, root: Path) -> "SkillRegistry":
        skills: dict[str, SkillDef] = {}
        if not root.exists():
            log.warning("Skills root %s does not exist", root)
            return cls(skills)

        for md in root.rglob("SKILL.md"):
            try:
                post = frontmatter.load(md)
            except Exception as e:
                log.error("Failed to parse %s: %s", md, e)
                continue
            meta = dict(post.metadata)
            name = meta.get("name") or md.parent.name
            desc = meta.get("description") or ""
            tools = meta.get("tools")
            if isinstance(tools, str):
                tools = [t.strip() for t in tools.split(",") if t.strip()]
            skill = SkillDef(
                name=name,
                description=desc,
                body=post.content.strip(),
                path=md,
                allowed_tools=tools,
                metadata=meta,
            )
            if name in skills:
                log.warning("Duplicate skill name %s (overwriting %s)", name, skills[name].path)
            skills[name] = skill
        log.info("Loaded %d skills from %s", len(skills), root)
        return cls(skills)
