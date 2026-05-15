from __future__ import annotations

from datetime import datetime, timezone

from ogcaibb.tracing.record import Trace, ToolCallRecord
from ogcaibb.tracing.redactor.ruleset import RuleSetRedactor, RuleSetRedactorConfig


def _trace(messages=None, tool_calls=None, assistant_text=""):
    return Trace(
        workstation_id="ws-0",
        daemon_version="0.0.0-test",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model="qwen-test",
        messages_in=messages or [],
        tool_calls=tool_calls or [],
        assistant_text=assistant_text,
    )


def test_scrubs_aws_access_key_in_assistant_text():
    t = _trace(assistant_text="My key is AKIA1234567890ABCDEF, please rotate.")
    r = RuleSetRedactor().redact(t)
    assert "AKIA1234567890ABCDEF" not in r.assistant_text
    assert "«REDACTED»" in r.assistant_text


def test_scrubs_assignment_style_token_in_messages():
    t = _trace(messages=[{"role": "user", "content": "use API_KEY=abcdefghijklmno12345"}])
    r = RuleSetRedactor().redact(t)
    assert "abcdefghijklmno12345" not in r.messages_in[0]["content"]


def test_drops_denied_path_in_tool_call():
    tc = ToolCallRecord(
        kind="ToolCallPart",
        data={"path": "/home/u/.aws/credentials", "args": {"content": "secret"}},
    )
    t = _trace(tool_calls=[tc])
    r = RuleSetRedactor().redact(t)
    out = r.tool_calls[0]
    assert out.data.get("_dropped") == "path on denylist"
    assert "secret" not in str(out.data)


def test_truncates_long_field():
    cfg = RuleSetRedactorConfig(max_field_chars=32)
    t = _trace(assistant_text="x" * 200)
    r = RuleSetRedactor(cfg).redact(t)
    assert len(r.assistant_text) < 100
    assert r.assistant_text.endswith("[truncated]")


def test_pem_block_collapsed():
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "abcdefghijabcdefghijabcdefghij\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    t = _trace(assistant_text=f"Here it is:\n{pem}\nDo not share.")
    r = RuleSetRedactor().redact(t)
    assert "BEGIN OPENSSH PRIVATE KEY" not in r.assistant_text
    assert "«REDACTED»" in r.assistant_text


def test_redactor_does_not_mutate_input():
    t = _trace(assistant_text="AKIA1234567890ABCDEF")
    r = RuleSetRedactor().redact(t)
    assert t.assistant_text == "AKIA1234567890ABCDEF"
    assert r is not t
