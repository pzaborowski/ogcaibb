"""ogcaibb-hub operator CLI."""

from __future__ import annotations

import hashlib
import secrets

import typer
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .config import settings

app = typer.Typer(help="ogcaibb-hub operator CLI")
console = Console()


@app.command()
def serve() -> None:
    """Run the hub HTTP server."""
    from .main import run

    run()


@app.command("issue-key")
def issue_key(
    name: str = typer.Option(..., "--name", help="Human-readable label, e.g. 'piotr-laptop'."),
    sub: str | None = typer.Option(
        None, "--sub", help="Stable identity (defaults to 'apikey:<name>')."
    ),
) -> None:
    """Generate a bearer token and print the YAML snippet to paste into keys.yaml.

    The token is printed ONCE — record it now; only its sha256 is ever stored.
    """
    token = "ogcaibb_pat_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    final_sub = sub or f"apikey:{name}"

    console.print(f"[bold]Token (give to the developer, store nowhere else):[/bold]\n{token}\n")
    snippet = yaml.safe_dump(
        {
            "keys": [
                {
                    "name": name,
                    "sub": final_sub,
                    "key_sha256": digest,
                    "revoked": False,
                }
            ]
        },
        sort_keys=False,
    )
    console.print(f"[bold]Append to {settings.apikeys_file}:[/bold]")
    console.print(Syntax(snippet, "yaml", theme="ansi_dark"))


@app.command("list-keys")
def list_keys() -> None:
    """Show entries in the keys file (without revealing tokens — only sha256)."""
    if not settings.apikeys_file.exists():
        console.print(f"[yellow]keys file not found: {settings.apikeys_file}[/yellow]")
        raise typer.Exit(0)
    data = yaml.safe_load(settings.apikeys_file.read_text(encoding="utf-8")) or {}
    table = Table(title=f"API keys in {settings.apikeys_file}")
    table.add_column("Name")
    table.add_column("Sub")
    table.add_column("Revoked")
    table.add_column("Sha256")
    for entry in data.get("keys") or []:
        table.add_row(
            str(entry.get("name", "")),
            str(entry.get("sub", "")),
            "yes" if entry.get("revoked") else "no",
            str(entry.get("key_sha256", ""))[:12] + "…",
        )
    console.print(table)


@app.command("count")
def count() -> None:
    """Report row counts in the index store."""
    import asyncio

    from .storage.registry import get_index_store

    async def _go():
        idx = get_index_store(settings.index_store, db_path=settings.index_store_path)
        try:
            total = await idx.count_traces()
        finally:
            await idx.close()
        console.print(f"traces in index: {total}")

    asyncio.run(_go())


if __name__ == "__main__":
    app()
