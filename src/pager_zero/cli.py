"""`pz` — the Pager-Zero command line.

Sub-commands are added phase by phase (see notes BUILD-PLAN.md). Phase 0 ships `doctor`.
"""

from __future__ import annotations

import os

import typer
from rich.console import Console
from rich.table import Table

from pager_zero import __version__
from pager_zero.config import settings
from pager_zero.llm import PROVIDER_KEY_ENV

app = typer.Typer(
    help="Pager-Zero: LLM on-call agent that diagnoses incidents and prepares the fix."
)
console = Console()


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context, version: bool = typer.Option(False, "--version", "-V")) -> None:
    if version:
        console.print(f"pager-zero {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@app.command()
def doctor(
    ping: bool = typer.Option(False, help="Also send a 1-token request to the model."),
) -> None:
    """Show effective config and check the LLM provider is reachable."""
    key_env = PROVIDER_KEY_ENV.get(settings.llm_provider)
    key_present = key_env is None or bool(os.environ.get(key_env))
    if key_env is None:
        key_status = "not needed"
    elif key_present:
        key_status = f"{key_env} ✔ set"
    else:
        key_status = f"{key_env} ✘ missing"

    t = Table(title="pager-zero doctor", show_header=False)
    t.add_row("version", __version__)
    t.add_row("llm provider", settings.llm_provider)
    t.add_row("llm model", settings.llm_model)
    t.add_row("api key", key_status)
    t.add_row("adapter backend", settings.adapter_backend.value)
    t.add_row("max steps", str(settings.max_steps))
    console.print(t)

    if not key_present:
        console.print(f"[red]Set {key_env} in .env or the environment.[/red]")
        raise typer.Exit(code=1)

    if ping:
        from pager_zero.llm import get_llm

        resp = get_llm().invoke("Reply with the single word: pong")
        console.print(f"model says: [green]{resp.content!s:.60}[/green]")


if __name__ == "__main__":
    app()
