"""System-prompt assembly tests.

The framework guarantee: an agent or skill prompt MUST NOT be able to silently
drop the daemon's critical rules (Write-tool mandate, workspace boundary,
pre-final file checklist). _resolve_system_prompt() is the chokepoint.
"""

from __future__ import annotations

from pathlib import Path

from ogcaibb.agents.registry import AgentRegistry
from ogcaibb.loop import (
    _CRITICAL_RULES_MARKER,
    _MENU_MARKER,
    _build_menu,
    _critical_rules,
    _default_system_prompt,
    _resolve_system_prompt,
)
from ogcaibb.skills.registry import SkillRegistry


def test_default_prompt_contains_critical_rules():
    assert _CRITICAL_RULES_MARKER in _default_system_prompt()


def test_custom_prompt_gets_rules_appended():
    agent_body = "You are a building-block-generator. Make folders."
    resolved = _resolve_system_prompt(agent_body)
    assert resolved.startswith(agent_body)
    assert _CRITICAL_RULES_MARKER in resolved
    # Rules come AFTER the agent body so they're most-recent for the model.
    assert resolved.index(agent_body) < resolved.index(_CRITICAL_RULES_MARKER)


def test_idempotent_when_marker_already_present():
    body = "x\n\n" + _critical_rules()
    resolved = _resolve_system_prompt(body)
    # Marker appears exactly once — no re-append.
    assert resolved.count(_CRITICAL_RULES_MARKER) == 1
    assert resolved.startswith("x")


def test_none_returns_default():
    assert _resolve_system_prompt(None) == _default_system_prompt()


def test_rules_mention_no_paste_and_checklist():
    """Regression guard for the qwen3:4b paste-in-markdown failure."""
    rules = _critical_rules()
    # Must explicitly forbid pasting file contents as code blocks.
    assert "MUST call the Write tool" in rules
    assert "code blocks" in rules
    # Must mandate pre-final verification.
    assert "Before producing your final answer" in rules
    assert "Write call" in rules


# --- menu builder + resolver-with-menu --------------------------------------


def _fake_skills(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: Catalogs alpha widgets\n---\nbody\n",
        encoding="utf-8",
    )
    (root / "beta").mkdir()
    (root / "beta" / "SKILL.md").write_text(
        "---\nname: beta-skill\ndescription: Beta vocabulary lookups\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry.load(root)


def _fake_agents(tmp_path: Path) -> AgentRegistry:
    root = tmp_path / "agents"
    root.mkdir(parents=True)
    (root / "gen.md").write_text(
        "---\nname: widget-generator\ndescription: Generates widget bblocks\n"
        "tools: Read, Write\n---\nbody\n",
        encoding="utf-8",
    )
    return AgentRegistry.load(root)


def _fake_commands(tmp_path: Path) -> Path:
    cdir = tmp_path / "commands"
    cdir.mkdir()
    (cdir / "make-widget.md").write_text(
        "---\ndescription: Create a widget bblock from a data sample\n---\nbody\n",
        encoding="utf-8",
    )
    (cdir / "no-frontmatter.md").write_text(
        "First **interesting** line\n\nMore body.\n",
        encoding="utf-8",
    )
    return cdir


def test_build_menu_returns_markdown_with_all_sections(tmp_path):
    menu = _build_menu(
        skills=_fake_skills(tmp_path),
        agents=_fake_agents(tmp_path),
        commands_dir=_fake_commands(tmp_path),
    )
    assert menu is not None
    assert _MENU_MARKER in menu
    assert "### Skills" in menu and "alpha-skill" in menu and "beta-skill" in menu
    assert "### Agents" in menu and "widget-generator" in menu
    assert "### Slash commands" in menu
    assert "/make-widget" in menu
    assert "/no-frontmatter" in menu
    # Fallback description from first non-empty body line (markdown emphasis
    # is preserved — the model handles it fine and it keeps the parser tiny).
    assert "First **interesting** line" in menu


def test_build_menu_returns_none_when_nothing_registered(tmp_path):
    empty_skills = SkillRegistry.load(tmp_path / "missing-skills")
    empty_agents = AgentRegistry.load(tmp_path / "missing-agents")
    assert _build_menu(skills=empty_skills, agents=empty_agents, commands_dir=None) is None


def test_resolve_system_prompt_with_menu_orders_correctly(tmp_path):
    menu = _build_menu(skills=_fake_skills(tmp_path))
    assert menu is not None

    resolved = _resolve_system_prompt("AGENT BODY", menu=menu)
    # Order: agent body → menu → critical rules.
    i_body = resolved.index("AGENT BODY")
    i_menu = resolved.index(_MENU_MARKER)
    i_rules = resolved.index(_CRITICAL_RULES_MARKER)
    assert i_body < i_menu < i_rules


def test_resolve_system_prompt_skips_menu_when_already_present(tmp_path):
    menu = _build_menu(skills=_fake_skills(tmp_path))
    body_with_menu = f"AGENT BODY\n\n{menu}"
    resolved = _resolve_system_prompt(body_with_menu, menu=menu)
    # Menu marker appears once even though we passed it both inline and as menu.
    assert resolved.count(_MENU_MARKER) == 1


def test_resolve_system_prompt_with_no_menu_unchanged():
    base = _resolve_system_prompt("AGENT BODY")
    with_none = _resolve_system_prompt("AGENT BODY", menu=None)
    assert base == with_none
