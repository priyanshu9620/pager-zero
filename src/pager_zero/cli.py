"""`pz` — the Pager-Zero command line.

Sub-commands are added phase by phase (see notes BUILD-PLAN.md). Phase 0 ships `doctor`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


world_app = typer.Typer(help="Inspect synthetic incident worlds.")
app.add_typer(world_app, name="world")


@world_app.command("list")
def world_list() -> None:
    """List scenario families."""
    from pager_zero.synth import list_scenarios

    for s in list_scenarios():
        console.print(s)


@world_app.command("show")
def world_show(
    scenario: str = typer.Option("bad-deploy", "--scenario", "-s"),
    seed: int = typer.Option(1, "--seed"),
    truth: bool = typer.Option(False, "--truth", help="Also reveal the hidden ground truth."),
    logs: int = typer.Option(8, help="How many of the most recent log lines to show."),
) -> None:
    """Generate a world and print what the agent would be able to see."""
    import json

    from rich.panel import Panel

    from pager_zero.synth import generate

    w = generate(scenario, seed)
    a = w.alert
    console.print(
        Panel(
            f"[bold]{a.name}[/bold]  service={a.service}  severity={a.severity}\n{a.summary}\n"
            f"fired {a.fired_at:%H:%M:%S}   now {w.now:%H:%M:%S}   window {int(w.window.total_seconds() // 60)}m",
            title=f"alert {a.id}  ({scenario} / seed {seed})",
        )
    )
    console.print_json(json.dumps(w.public_summary(), default=str))
    if logs:
        t = Table(title=f"last {logs} log lines ({len(w.logs)} total)")
        t.add_column("ts"), t.add_column("service"), t.add_column("level"), t.add_column("message")
        for line in w.logs[-logs:]:
            t.add_row(f"{line.ts:%H:%M:%S}", line.service, line.level, line.message[:90])
        console.print(t)
    if truth:
        g = w.ground_truth
        console.print(
            Panel(
                f"[bold]{g.cause}[/bold]\nculprit: {g.culprit}\nfix: {g.fix_action}({g.fix_target})"
                + (
                    f"\nalso accepted: {g.acceptable_alternatives}"
                    if g.acceptable_alternatives
                    else ""
                ),
                title="GROUND TRUTH (hidden from the agent)",
                border_style="red",
            )
        )


mcp_app = typer.Typer(help="Run the MCP tool server.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    scenario: str = typer.Option("bad-deploy", "--scenario", "-s"),
    seed: int = typer.Option(1, "--seed"),
    state: Path | None = typer.Option(
        None, "--state", help="Persist the world here so other processes see mutations."
    ),
    actions: bool = typer.Option(
        False, "--actions", help="Expose mutating tools. Off by default (D-12)."
    ),
) -> None:
    """Serve incident tools over stdio (synthetic backend). Point any MCP client at this.

    Example Claude Code config:  {"command": "uv", "args": ["run", "pz", "mcp", "serve"]}
    """
    from pager_zero.config import AdapterBackend, settings
    from pager_zero.mcp_server.server import synthetic_server

    if settings.adapter_backend is not AdapterBackend.synthetic:
        console.print("[red]live backend not implemented yet (phase 7)[/red]", file=sys.stderr)
        raise typer.Exit(code=2)
    synthetic_server(scenario, seed, state, actions_enabled=actions).run(transport="stdio")


if __name__ == "__main__":
    app()
