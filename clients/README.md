# Client configs

Both clients point at the ogcaibb daemon (`http://localhost:4141/v1`). The daemon
fronts the shared Ollama server with agents, skills, slash commands, memory, and
Docker-backed validators.

## Continue.dev (VS Code, JetBrains)

```bash
cp continue/config.yaml ~/.continue/config.yaml
# or, per workspace:
cp continue/config.yaml .continue/config.yaml
```

Reload VS Code. The chat panel will list `ogcaibb-chat` as a model. Slash commands
defined in `prompts:` will appear in the slash-command picker; the daemon expands
them server-side using the matching markdown file in `commands/`.

## aider (terminal)

```bash
cp aider/.aider.conf.yml .          # in a bblock repo
aider
```

## Pointing a bblock repo at ogcaibb

The daemon discovers manifests from `OGCAIBB_MANIFESTS_ROOT`, which defaults to
this repo. To use a different manifest set (e.g. one curated per project) start
the daemon with:

```bash
OGCAIBB_MANIFESTS_ROOT=/path/to/your/manifests ogcaibb-daemon
```

A bblock repo does not need to vendor any manifests of its own — `ogcaibb` ships
the canonical set.
