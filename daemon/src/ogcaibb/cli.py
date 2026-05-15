"""Thin CLI for operator tasks: serve, ping Ollama, list skills/agents/commands."""

from __future__ import annotations

import asyncio
import gzip
import json

import typer
from rich.console import Console
from rich.table import Table

from .agents.registry import AgentRegistry
from .config import settings
from .ollama_client import OllamaUnavailable, get_client
from .skills.registry import SkillRegistry

app = typer.Typer(help="ogcaibb operator CLI")
traces_app = typer.Typer(help="Inspect locally captured traces")
app.add_typer(traces_app, name="traces")
console = Console()


@app.command()
def serve() -> None:
    """Run the daemon (FastAPI + agent loop)."""
    from .main import run

    run()


@app.command()
def ping() -> None:
    """Health-check the configured Ollama host."""

    async def _go():
        try:
            info = await get_client().health()
            console.print(json.dumps(info, indent=2))
        except OllamaUnavailable as e:
            console.print(f"[red]Ollama unreachable: {e}[/red]")
            raise typer.Exit(1) from e

    asyncio.run(_go())


@app.command(name="skills")
def list_skills() -> None:
    """List discovered skills."""
    reg = SkillRegistry.load(settings.manifests_root / "skills")
    table = Table(title=f"Skills ({len(reg)})")
    table.add_column("Name")
    table.add_column("Description", overflow="fold")
    for s in reg:
        table.add_row(s.name, s.description[:160])
    console.print(table)


@app.command(name="agents")
def list_agents() -> None:
    """List discovered agents."""
    reg = AgentRegistry.load(settings.manifests_root / "agents")
    table = Table(title=f"Agents ({len(reg)})")
    table.add_column("Name")
    table.add_column("Model")
    table.add_column("Description", overflow="fold")
    for a in reg:
        table.add_row(a.name, a.model or "(default)", a.description[:120])
    console.print(table)


@app.command(name="commands")
def list_commands() -> None:
    """List discovered slash commands."""
    cdir = settings.manifests_root / "commands"
    table = Table(title=f"Commands ({sum(1 for _ in cdir.glob('*.md'))})")
    table.add_column("Slash")
    table.add_column("File")
    for p in sorted(cdir.glob("*.md")):
        table.add_row(f"/{p.stem}", str(p.relative_to(cdir.parent)))
    console.print(table)


@traces_app.command("status")
def traces_status() -> None:
    """Show WAL stats: open chunk, sealed backlog, uploaded count."""
    from .tracing.wal import WAL

    wal = WAL(
        settings.trace_dir,
        max_bytes=settings.trace_max_bytes,
        max_age_seconds=settings.trace_max_age_seconds,
        max_total_bytes=settings.trace_max_total_bytes,
    )
    try:
        stats = wal.stats()
    finally:
        wal.close()
    table = Table(title=f"Trace WAL @ {settings.trace_dir}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)


@traces_app.command("tail")
def traces_tail(
    n: int = typer.Option(5, "--limit", "-n", help="How many recent trace records to show."),
    show_messages: bool = typer.Option(
        False, "--messages", help="Include input messages (verbose)."
    ),
) -> None:
    """Stream the most recent trace records from sealed + open chunks."""
    records = _collect_recent_records(n)
    if not records:
        console.print("[yellow]No trace records found.[/yellow]")
        raise typer.Exit(0)
    for rec in records:
        if rec.get("kind") != "trace":
            continue
        p = rec["payload"]
        title = (
            f"trace_id={p['trace_id']}  model={p['model']}  "
            f"agent={p.get('agent')}  skill={p.get('skill')}"
        )
        console.rule(title)
        console.print(f"[dim]{p['started_at']} → {p['ended_at']}[/dim]")
        if p.get("incomplete"):
            console.print(f"[red]incomplete[/red] error={p.get('error')}")
        if show_messages:
            console.print("[bold]messages_in:[/bold]")
            console.print(json.dumps(p.get("messages_in", []), indent=2)[:4000])
        text = p.get("assistant_text") or ""
        if text:
            console.print(f"[bold]assistant:[/bold] {text[:800]}")
        tcs = p.get("tool_calls") or []
        if tcs:
            console.print(f"[bold]tool_calls:[/bold] {len(tcs)}")


def _collect_recent_records(n: int) -> list[dict]:
    """Read up to N most recent trace records from disk, newest last."""
    trace_dir = settings.trace_dir.expanduser()
    if not trace_dir.exists():
        return []
    records: list[dict] = []
    sealed = sorted(trace_dir.glob("sealed-*.jsonl.gz"), reverse=True)
    open_chunks = sorted(trace_dir.glob("open-*.jsonl"), reverse=True)
    for path in (*open_chunks, *sealed):
        try:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as fp:  # type: ignore[arg-type]
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
        if len(records) >= n:
            break
    return records[-n:]


if __name__ == "__main__":
    app()
