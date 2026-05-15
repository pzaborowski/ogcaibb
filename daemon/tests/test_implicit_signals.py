"""Unit tests for implicit signal detectors and the SignalObserver.

These exercise the detectors directly against crafted SignalContexts so we
don't depend on the full daemon being up. The observer is tested end-to-end
with a fake zero-delay detector that asserts WAL persistence.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ogcaibb.tracing.record import Signal, ToolCallRecord, Trace
from ogcaibb.tracing.signals.base import ImplicitSignal, SignalContext
from ogcaibb.tracing.signals.edit_retention import EditRetention
from ogcaibb.tracing.signals.git_commit import GitCommit
from ogcaibb.tracing.signals.observer import SignalObserver
from ogcaibb.tracing.signals.tool_call_completed_clean import ToolCallCompletedClean
from ogcaibb.tracing.wal import WAL


def _ctx(
    *,
    tool_calls: list[ToolCallRecord] | None = None,
    assistant_text: str = "ok",
    incomplete: bool = False,
    error: str | None = None,
    workspace_root: Path | None = None,
) -> SignalContext:
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    return SignalContext(
        trace_id="t-1",
        workstation_id="ws-1",
        workspace_root=workspace_root,
        started_at=now,
        ended_at=now,
        assistant_text=assistant_text,
        tool_calls=tool_calls or [],
        error=error,
        incomplete=incomplete,
    )


# --- tool_call_completed_clean ----------------------------------------------


@pytest.mark.asyncio
async def test_clean_detector_emits_positive_when_no_errors():
    sigs = await ToolCallCompletedClean().detect(_ctx())
    assert len(sigs) == 1
    assert sigs[0].polarity == 1
    assert sigs[0].source == "tool_call_completed_clean"


@pytest.mark.asyncio
async def test_clean_detector_silent_on_incomplete():
    assert await ToolCallCompletedClean().detect(_ctx(incomplete=True)) == []
    assert await ToolCallCompletedClean().detect(_ctx(error="boom")) == []


@pytest.mark.asyncio
async def test_clean_detector_silent_when_tool_returned_error():
    tc = ToolCallRecord(kind="ToolReturnPart", data={"content": "Error: nope"})
    assert await ToolCallCompletedClean().detect(_ctx(tool_calls=[tc])) == []


# --- edit_retention ----------------------------------------------------------


def _write_call(path: str, content: str) -> ToolCallRecord:
    return ToolCallRecord(
        kind="ToolCallPart",
        data={"tool_name": "Write", "args": {"file_path": path, "content": content}},
    )


def _edit_call(path: str, new_string: str) -> ToolCallRecord:
    return ToolCallRecord(
        kind="ToolCallPart",
        data={
            "tool_name": "Edit",
            "args": {"file_path": path, "old_string": "x", "new_string": new_string},
        },
    )


@pytest.mark.asyncio
async def test_edit_retention_positive_when_content_retained(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hello world\n", encoding="utf-8")
    ctx = _ctx(
        tool_calls=[_write_call(str(target), "hello world\n")],
        workspace_root=tmp_path,
    )
    sigs = await EditRetention(delay_seconds=0).detect(ctx)
    assert sigs[0].polarity == 1


@pytest.mark.asyncio
async def test_edit_retention_negative_when_file_deleted(tmp_path):
    ctx = _ctx(
        tool_calls=[_write_call(str(tmp_path / "gone.txt"), "anything")],
        workspace_root=tmp_path,
    )
    sigs = await EditRetention(delay_seconds=0).detect(ctx)
    assert sigs[0].polarity == -1


@pytest.mark.asyncio
async def test_edit_retention_neutral_on_modification(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hello world\nplus user changes\n", encoding="utf-8")
    ctx = _ctx(
        tool_calls=[_write_call(str(target), "completely different original\n")],
        workspace_root=tmp_path,
    )
    sigs = await EditRetention(delay_seconds=0).detect(ctx)
    assert sigs[0].polarity == 0


@pytest.mark.asyncio
async def test_edit_retention_edit_substring_check(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("intro\nNEW_LINE\noutro\n", encoding="utf-8")
    ctx = _ctx(
        tool_calls=[_edit_call(str(target), "NEW_LINE")],
        workspace_root=tmp_path,
    )
    sigs = await EditRetention(delay_seconds=0).detect(ctx)
    assert sigs[0].polarity == 1


@pytest.mark.asyncio
async def test_edit_retention_ignores_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"  # not under tmp_path
    ctx = _ctx(
        tool_calls=[_write_call(str(outside), "content")],
        workspace_root=tmp_path,
    )
    sigs = await EditRetention(delay_seconds=0).detect(ctx)
    assert sigs == []


# --- git_commit --------------------------------------------------------------


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


@pytest.mark.asyncio
async def test_git_commit_positive_when_commit_lands_after_turn(git_repo):
    f = git_repo / "hello.txt"
    f.write_text("v1\n", encoding="utf-8")
    _git(git_repo, "add", "hello.txt")
    _git(git_repo, "commit", "-q", "-m", "initial")

    # Trace finishes BEFORE the second commit
    ended = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ctx = SignalContext(
        trace_id="t-git", workstation_id="ws-1",
        workspace_root=git_repo,
        started_at=ended, ended_at=ended,
        tool_calls=[_write_call("hello.txt", "v2")],
    )

    f.write_text("v2\n", encoding="utf-8")
    _git(git_repo, "add", "hello.txt")
    _git(git_repo, "commit", "-q", "-m", "post-turn")

    sigs = await GitCommit(delay_seconds=0).detect(ctx)
    assert sigs and sigs[0].polarity == 1
    assert sigs[0].meta["files"][0]["commits"] >= 1


@pytest.mark.asyncio
async def test_git_commit_zero_when_no_commit_in_window(git_repo):
    f = git_repo / "hello.txt"
    f.write_text("v1\n", encoding="utf-8")
    _git(git_repo, "add", "hello.txt")
    _git(git_repo, "commit", "-q", "-m", "initial")

    # ended_at is in the FUTURE → since-filter excludes the initial commit
    ended = datetime(2099, 1, 1, tzinfo=timezone.utc)
    ctx = SignalContext(
        trace_id="t-git", workstation_id="ws-1",
        workspace_root=git_repo,
        started_at=ended, ended_at=ended,
        tool_calls=[_write_call("hello.txt", "v2")],
    )
    sigs = await GitCommit(delay_seconds=0).detect(ctx)
    assert sigs and sigs[0].polarity == 0


@pytest.mark.asyncio
async def test_git_commit_silent_when_no_git_repo(tmp_path):
    ctx = SignalContext(
        trace_id="t-git", workstation_id="ws-1",
        workspace_root=tmp_path,
        started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        tool_calls=[_write_call("hello.txt", "v2")],
    )
    sigs = await GitCommit(delay_seconds=0).detect(ctx)
    assert sigs == []


# --- SignalObserver ----------------------------------------------------------


class _FakeDetector:
    name = "fake"
    delay_seconds = 0.0

    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, ctx: SignalContext) -> list[Signal]:
        self.calls += 1
        return [
            Signal(
                trace_id=ctx.trace_id,
                workstation_id=ctx.workstation_id,
                source=self.name,
                polarity=1,
                weight=0.5,
                detected_at=ctx.ended_at,
            )
        ]


@pytest.mark.asyncio
async def test_observer_runs_detector_and_persists_signal(tmp_path):
    wal = WAL(tmp_path, max_bytes=10_000, max_age_seconds=600)
    try:
        det = _FakeDetector()
        observer = SignalObserver(
            wal=wal, detectors=[det], workstation_id="ws-x",
        )
        trace = Trace(
            trace_id="t-obs",
            workstation_id="ws-x",
            daemon_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model="qwen-test",
        )
        observer.schedule(trace)
        # Let the zero-delay task run.
        await asyncio.sleep(0.05)
        await observer.stop()

        assert det.calls == 1
        wal.seal_now()
        chunks = wal.list_sealed()
        assert chunks
        with gzip.open(chunks[0].path, "rt") as fp:
            recs = [json.loads(line) for line in fp if line.strip()]
        kinds = [r["kind"] for r in recs]
        assert "signal" in kinds
        sig_records = [r["payload"] for r in recs if r["kind"] == "signal"]
        assert sig_records[0]["source"] == "fake"
        assert sig_records[0]["polarity"] == 1
    finally:
        wal.close()


@pytest.mark.asyncio
async def test_observer_stop_cancels_pending_detectors(tmp_path):
    class _SlowDetector:
        name = "slow"
        delay_seconds = 30.0

        async def detect(self, ctx: SignalContext) -> list[Signal]:
            return []

    wal = WAL(tmp_path, max_bytes=10_000, max_age_seconds=600)
    try:
        observer = SignalObserver(
            wal=wal, detectors=[_SlowDetector()], workstation_id="ws-x",
        )
        trace = Trace(
            trace_id="t-slow",
            workstation_id="ws-x",
            daemon_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model="qwen-test",
        )
        observer.schedule(trace)
        # Stop immediately — task should not hang the test.
        await asyncio.wait_for(observer.stop(), timeout=2.0)
    finally:
        wal.close()
