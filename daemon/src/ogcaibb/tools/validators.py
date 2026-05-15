"""Schema + JSON-LD context validators (in-process, no Docker)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from ..config import settings
from .registry import register


def _abs(rel: str) -> Path:
    root = settings.workspace_root.expanduser().resolve()
    p = Path(rel).expanduser()
    return (root / p).resolve() if not p.is_absolute() else p.resolve()


@register(
    "ValidateAgainstSchema",
    "Validate a JSON instance file against a JSON Schema file. "
    "Returns 'ok' or a list of validation errors.",
)
async def validate_against_schema(instance_path: str, schema_path: str) -> str:
    inst = json.loads(_abs(instance_path).read_text())
    schema = json.loads(_abs(schema_path).read_text())
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(inst), key=lambda e: list(e.absolute_path))
    if not errors:
        return "ok"
    return "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)


@register(
    "CheckContextCompleteness",
    "For a JSON-LD instance, list any property keys that lack an @id mapping "
    "in the supplied context.jsonld file.",
)
async def check_context_completeness(instance_path: str, context_path: str) -> str:
    inst = json.loads(_abs(instance_path).read_text())
    ctx_doc = json.loads(_abs(context_path).read_text())
    ctx = ctx_doc.get("@context", {})
    mapped = set()
    if isinstance(ctx, dict):
        mapped = {k for k, v in ctx.items() if isinstance(v, (str, dict))}
    elif isinstance(ctx, list):
        for c in ctx:
            if isinstance(c, dict):
                mapped.update(c.keys())

    missing: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("@"):
                    walk(v)
                    continue
                if k not in mapped:
                    missing.add(k)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(inst)
    if not missing:
        return "ok"
    return "missing @id mappings for: " + ", ".join(sorted(missing))
