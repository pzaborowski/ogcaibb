#!/usr/bin/env python3
"""Generate a one-file inventory of ogcaibb agents, skills, and commands."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Entry:
    name: str
    path: Path
    description: str = "TODO"
    model: str = ""
    tools: str = ""
    argument_hint: str = ""
    group: str = ""


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip()
    data: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            if current_key is not None:
                data[current_key] = " ".join(x.strip() for x in current_lines).strip()
            current_key = match.group(1)
            value = match.group(2).strip()
            current_lines = [value] if value not in {"|", ">"} else []
        elif current_key is not None:
            current_lines.append(line.strip())
    if current_key is not None:
        data[current_key] = " ".join(x.strip() for x in current_lines).strip()
    return data, body


def first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return "TODO"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read_entry(path: Path, root: Path, default_name: str) -> tuple[dict[str, str], str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(text)
    title = fm.get("name") or first_heading(body) or default_name
    return fm, body, title


def iter_files(root: Path, pattern: str) -> Iterable[Path]:
    return sorted(root.glob(pattern), key=lambda p: p.as_posix())


def collect_agents(root: Path) -> list[Entry]:
    agents: list[Entry] = []
    for path in iter_files(root, "agents/**/*.md"):
        if path.name.lower() == "readme.md":
            continue
        fm, body, title = read_entry(path, root, path.stem)
        group = path.parent.relative_to(root / "agents").as_posix()
        agents.append(
            Entry(
                name=fm.get("name") or title,
                path=path,
                description=fm.get("description") or first_heading(body),
                model=fm.get("model", ""),
                tools=fm.get("tools", ""),
                group=group,
            )
        )
    return agents


def collect_skills(root: Path) -> list[Entry]:
    skills: list[Entry] = []
    for path in iter_files(root, "skills/*/SKILL.md"):
        fm, body, title = read_entry(path, root, path.parent.name)
        skills.append(
            Entry(
                name=fm.get("name") or path.parent.name,
                path=path,
                description=fm.get("description") or first_heading(body),
                group=path.parent.name,
            )
        )
    return skills


def collect_commands(root: Path) -> list[Entry]:
    commands: list[Entry] = []
    for path in iter_files(root, "commands/*.md"):
        fm, body, title = read_entry(path, root, path.stem)
        commands.append(
            Entry(
                name="/" + path.stem,
                path=path,
                description=fm.get("description") or first_heading(body),
                argument_hint=fm.get("argument-hint", ""),
            )
        )
    return commands


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        clean = [cell.replace("\n", " ").replace("|", "\\|") if cell else "" for cell in row]
        out.append("| " + " | ".join(clean) + " |")
    return "\n".join(out)


def generate(root: Path, output: Path) -> str:
    agents = collect_agents(root)
    skills = collect_skills(root)
    commands = collect_commands(root)

    lines: list[str] = []
    lines.append("# ogcaibb Agents, Skills, and Commands")
    lines.append("")
    lines.append("Generated inventory of the repository automation surface: agents, skills, and slash commands.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(table(["Type", "Count", "Location"], [
        ["Agents", str(len(agents)), "`agents/**/*.md`"],
        ["Skills", str(len(skills)), "`skills/*/SKILL.md`"],
        ["Commands", str(len(commands)), "`commands/*.md`"],
    ]))
    lines.append("")
    lines.append("## Directory Map")
    lines.append("")
    lines.append("```text")
    lines.append("agents/    Agent definitions grouped by role: converters, generators, integrator, validators.")
    lines.append("skills/    Reusable procedural skills, each with a required SKILL.md.")
    lines.append("commands/  Slash-command wrappers that route common tasks to agents or skills.")
    lines.append("```")
    lines.append("")
    lines.append("## Agents")
    lines.append("")
    for group in sorted({a.group for a in agents}):
        group_agents = [a for a in agents if a.group == group]
        lines.append(f"### {group}")
        lines.append("")
        lines.append(table(
            ["Agent", "Path", "Model", "Tools", "Description"],
            [[a.name, f"`{rel(a.path, root)}`", a.model, a.tools, a.description] for a in group_agents],
        ))
        lines.append("")
    lines.append("## Skills")
    lines.append("")
    lines.append(table(
        ["Skill", "Path", "Description"],
        [[s.name, f"`{rel(s.path, root)}`", s.description] for s in skills],
    ))
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    lines.append(table(
        ["Command", "Path", "Arguments", "Description"],
        [[c.name, f"`{rel(c.path, root)}`", c.argument_hint, c.description] for c in commands],
    ))
    lines.append("")
    lines.append("## Dispatch Pattern")
    lines.append("")
    lines.append("- Commands are thin task entry points for common workflows.")
    lines.append("- Agents own larger task behavior, such as generation, conversion, integration, or validation.")
    lines.append("- Skills provide reusable procedural knowledge and deterministic helper scripts.")
    lines.append("- When a task has both a command and a skill, the command is the user-facing shortcut and the skill is the reusable implementation guidance.")
    lines.append("")
    lines.append("## Maintenance Notes")
    lines.append("")
    lines.append("- Add agents under `agents/<group>/<name>.md` with frontmatter `name`, `description`, `tools`, and `model` where applicable.")
    lines.append("- Add skills under `skills/<name>/SKILL.md` with frontmatter `name` and `description`.")
    lines.append("- Add commands under `commands/<name>.md` with frontmatter `description` and optional `argument-hint`.")
    lines.append("- Regenerate this document with `python3 skills/repo-structure-documenter/scripts/document_repo_structure.py --repo . --output docs/AGENTS_SKILLS_COMMANDS.md`.")
    lines.append("")
    result = "\n".join(lines)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--output", default="docs/AGENTS_SKILLS_COMMANDS.md", help="Output Markdown path")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    generate(root, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
