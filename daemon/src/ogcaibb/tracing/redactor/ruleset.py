"""Default redactor: regex secret patterns + path denylist + length cap.

The defaults catch the common cases (AWS keys, bearer tokens, generic
"FOO=secretvalue" pairs, common credential file paths). The full ruleset is
overridable via a YAML file referenced by `OGCAIBB_REDACTOR_RULES`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..record import Trace, ToolCallRecord

log = logging.getLogger(__name__)

REDACTED = "«REDACTED»"
TRUNCATED_SUFFIX = "…[truncated]"

# Patterns are intentionally conservative — false negatives are preferable to
# false positives in a code-assistant context where literal source matters.
DEFAULT_SECRET_PATTERNS: list[str] = [
    # AWS Access Key ID
    r"AKIA[0-9A-Z]{16}",
    # GitHub fine-grained / classic tokens
    r"gh[pousr]_[A-Za-z0-9_]{20,}",
    # Slack tokens
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    # Generic bearer / api key assignments:
    #   API_KEY="abc123…", token: 'sk-…', SECRET=foo
    r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.\/+=]{12,}['\"]?",
    # JWT-ish
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    # Private-key PEM markers (any content between BEGIN/END is collapsed)
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
]

DEFAULT_PATH_DENY: list[str] = [
    r"(^|/)\.env(\.|$)",
    r"(^|/)\.aws/credentials($|/)",
    r"(^|/)\.ssh/id_[a-z0-9_]+$",
    r"(^|/)id_rsa($|\.pub$)",
    r"\.pem$",
    r"\.p12$",
    r"\.pfx$",
    r"\.kdbx$",
    r"(^|/)secrets?\.(ya?ml|json|toml|env)$",
]

DEFAULT_MAX_FIELD_CHARS = 16 * 1024  # 16 KiB per content field


@dataclass
class RuleSetRedactorConfig:
    secret_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_SECRET_PATTERNS))
    path_denylist: list[str] = field(default_factory=lambda: list(DEFAULT_PATH_DENY))
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    workspace_root: Path | None = None  # used to flag content from outside the workspace

    @classmethod
    def load_yaml(cls, path: Path) -> "RuleSetRedactorConfig":
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            secret_patterns=data.get("secret_patterns", DEFAULT_SECRET_PATTERNS),
            path_denylist=data.get("path_denylist", DEFAULT_PATH_DENY),
            max_field_chars=int(data.get("max_field_chars", DEFAULT_MAX_FIELD_CHARS)),
            workspace_root=Path(data["workspace_root"]).expanduser()
            if data.get("workspace_root")
            else None,
        )


class RuleSetRedactor:
    name = "ruleset"

    def __init__(self, config: RuleSetRedactorConfig | None = None) -> None:
        self.cfg = config or RuleSetRedactorConfig()
        self._secret_res = [re.compile(p) for p in self.cfg.secret_patterns]
        self._path_deny_res = [re.compile(p) for p in self.cfg.path_denylist]

    # --- public surface -------------------------------------------------

    def redact(self, trace: Trace) -> Trace:
        return trace.model_copy(
            update={
                "messages_in": [self._redact_message(m) for m in trace.messages_in],
                "tool_calls": [self._redact_tool_call(tc) for tc in trace.tool_calls],
                "assistant_text": self._scrub(trace.assistant_text),
                "reasoning": self._scrub(trace.reasoning) if trace.reasoning else trace.reasoning,
            }
        )

    # --- helpers --------------------------------------------------------

    def _redact_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        out = dict(msg)
        content = out.get("content")
        if isinstance(content, str):
            out["content"] = self._scrub(content)
        return out

    def _redact_tool_call(self, tc: ToolCallRecord) -> ToolCallRecord:
        data = dict(tc.data)
        # Drop entire payload if it targets a denylisted path.
        path_value = self._extract_path_field(data)
        if path_value and self._path_denied(path_value):
            return ToolCallRecord(
                kind=tc.kind,
                data={"path": path_value, "_dropped": "path on denylist"},
            )
        # Scrub all string leaves.
        return ToolCallRecord(kind=tc.kind, data=self._scrub_obj(data))

    def _scrub_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self._scrub(obj)
        if isinstance(obj, list):
            return [self._scrub_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self._scrub_obj(v) for k, v in obj.items()}
        return obj

    def _scrub(self, text: str) -> str:
        if not text:
            return text
        for rx in self._secret_res:
            text = rx.sub(REDACTED, text)
        if len(text) > self.cfg.max_field_chars:
            text = text[: self.cfg.max_field_chars] + TRUNCATED_SUFFIX
        return text

    def _path_denied(self, path: str) -> bool:
        return any(rx.search(path) for rx in self._path_deny_res)

    @staticmethod
    def _extract_path_field(data: dict[str, Any]) -> str | None:
        for key in ("path", "file_path", "filepath", "filename"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
        # PydanticAI ToolCallPart args may be JSON or dict
        args = data.get("args")
        if isinstance(args, dict):
            for key in ("path", "file_path", "filepath", "filename"):
                v = args.get(key)
                if isinstance(v, str) and v:
                    return v
        return None
