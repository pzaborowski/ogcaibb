---
name: repo-structure-documenter
description: Use when asked to document the ogcaibb repository structure, especially to produce one Markdown file that inventories agents, skills, and slash commands. Scans `agents/**/*.md`, `skills/*/SKILL.md`, and `commands/*.md`, extracts frontmatter metadata and first headings, groups agents by folder, and writes a concise structure document with counts, tables, and maintenance notes.
---

# Repo Structure Documenter

Generate or refresh a single Markdown document describing the `ogcaibb` agent,
skill, and command inventory.

## Default output

Write to:

```text
docs/AGENTS_SKILLS_COMMANDS.md
```

If the user provides another path, use that path.

## Workflow

1. Work from the repository root unless the user provides `repo=...`.
2. Run the bundled script:

   ```bash
   python3 skills/repo-structure-documenter/scripts/document_repo_structure.py \
     --repo . \
     --output docs/AGENTS_SKILLS_COMMANDS.md
   ```

3. Review the generated Markdown for obvious omissions.
4. Report the output path and the counts of agents, skills, and commands.

## What to include

The generated document should include:

- Repository purpose summary.
- Directory map for `agents/`, `skills/`, and `commands/`.
- Agents grouped by subdirectory, with name, path, model, tools, and description.
- Skills with name, path, and description from `SKILL.md` frontmatter.
- Commands with slash-command name, path, argument hint, and description.
- Crosswalk explaining how commands normally dispatch to agents/skills.
- Maintenance notes explaining how to add new agents, skills, and commands.

## Rules

- Do not invent descriptions when frontmatter is missing; use the first heading
  or mark the field as `TODO`.
- Do not scan private runtime folders such as `.git`, caches, `remote_ollama`,
  `node_modules`, or virtual environments.
- Keep the output as one Markdown file.
- Preserve existing unrelated repository changes.
