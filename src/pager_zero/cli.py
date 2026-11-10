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
    alert_service: str = typer.Option("", help="live backend: alerting service"),
    alert_name: str = typer.Option("HighLatencyP99", help="live backend: alert name"),
    alert_summary: str = typer.Option("", help="live backend: alert summary"),
    alert_labels: str = typer.Option("{}", help="live backend: alert labels as JSON"),
) -> None:
    """Serve incident tools over stdio. Point any MCP client at this.

    Backend is chosen by ADAPTER_BACKEND (synthetic | live). Example Claude Code config:
    {"command": "uv", "args": ["run", "pz", "mcp", "serve"]}
    """
    from pager_zero.config import AdapterBackend, settings
    from pager_zero.mcp_server.server import build_server, synthetic_server

    if settings.adapter_backend is AdapterBackend.synthetic:
        synthetic_server(scenario, seed, state, actions_enabled=actions).run(transport="stdio")
        return
    from pager_zero.adapters.live import LiveAdapters, live_incident

    if not alert_service:
        console.print("[red]live backend needs --alert-service[/red]", file=sys.stderr)
        raise typer.Exit(code=2)
    import json

    inc = live_incident(
        alert_service,
        alert_name,
        alert_summary or f"{alert_name} on {alert_service}",
        labels=json.loads(alert_labels),
    )
    build_server(LiveAdapters.from_settings(), actions_enabled=actions, incident=inc).run(
        transport="stdio"
    )


incident_app = typer.Typer(help="Run the agent on an incident.")
app.add_typer(incident_app, name="incident")


@incident_app.command("fire")
def incident_fire(
    scenario: str = typer.Option(
        "random",
        "--scenario",
        "-s",
        help="Scenario family, or `random` (blind: hidden until over).",
    ),
    seed: int | None = typer.Option(None, "--seed", help="Deterministic seed; omitted → random."),
) -> None:
    """Generate a synthetic incident and run the agent until it needs approval."""
    import asyncio

    from rich.panel import Panel

    from pager_zero.agent.runner import new_incident, public_meta, run_until_approval

    meta = new_incident(scenario, seed)
    shown = public_meta(meta)
    console.print(
        f"▶ incident [bold]{meta['id']}[/bold]  scenario={shown['scenario']} seed={shown['seed']}  "
        f"model={settings.llm_provider}/{settings.llm_model}"
        + (
            "  [dim](blind — revealed by `pz incident show` once resolved)[/dim]"
            if meta["blind"]
            else ""
        )
    )
    state = asyncio.run(run_until_approval(meta, console))
    status = state.get("status")
    if status == "awaiting_approval":
        console.print(
            Panel(
                f"pz approve {meta['id']}        apply the proposed fix\n"
                f"pz reject  {meta['id']} --reason '...'",
                title="⏸  approval required",
                border_style="yellow",
            )
        )
    else:
        console.print(f"[yellow]finished with status={status} (no fix proposed)[/yellow]")


def _decide(incident_id: str, decision: str, note: str) -> None:
    import asyncio

    from pager_zero.agent.runner import load_meta, resume

    meta = load_meta(incident_id)
    if meta["status"] != "awaiting_approval":
        console.print(f"[red]{incident_id} is {meta['status']}, not awaiting approval[/red]")
        raise typer.Exit(code=1)
    state = asyncio.run(resume(meta, {"decision": decision, "note": note}, console))
    console.print(f"[bold]{incident_id}[/bold] → {state.get('status')}")


@incident_app.command("approve")
def incident_approve(incident_id: str, note: str = typer.Option("", "--note")) -> None:
    """Approve the proposed fix: execute it and verify recovery."""
    _decide(incident_id, "approve", note)


@incident_app.command("reject")
def incident_reject(incident_id: str, reason: str = typer.Option("", "--reason")) -> None:
    """Reject the proposed fix; the incident is left for a human."""
    _decide(incident_id, "reject", reason)


@incident_app.command("list")
def incident_list() -> None:
    """List incidents and their status."""
    from pager_zero.agent.runner import list_incidents, public_meta

    t = Table()
    for col in ("id", "scenario", "seed", "status", "model", "created"):
        t.add_column(col)
    for m in map(public_meta, list_incidents()):
        t.add_row(
            m["id"],
            m["scenario"],
            str(m["seed"]),
            m["status"],
            f"{m['provider']}/{m['model']}",
            m["created"],
        )
    console.print(t)


@incident_app.command("show")
def incident_show(incident_id: str) -> None:
    """Show diagnosis / plan / result for an incident."""
    import asyncio
    import json

    from pager_zero.agent.runner import current_state, load_meta, public_meta, truth_check

    meta = load_meta(incident_id)
    st = asyncio.run(current_state(meta))
    shown = public_meta(meta)
    console.print(f"[bold]scenario[/bold]: {shown['scenario']}  [bold]seed[/bold]: {shown['seed']}")
    for key in ("status", "steps", "diagnosis", "plan", "approval", "execution", "verification"):
        if key in st:
            console.print(
                f"[bold]{key}[/bold]: {json.dumps(st[key], indent=1, default=str) if isinstance(st[key], dict) else st[key]}"
            )
    truth = truth_check(meta, st)
    if truth:
        ok = lambda b: "[green]✔[/green]" if b else "[red]✘[/red]"  # noqa: E731
        console.print(
            f"[bold]ground truth[/bold]: {truth['planted']['family']} — {truth['planted']['cause']}\n"
            f"  expected fix: {truth['planted']['fix']['action']}({truth['planted']['fix']['target']})\n"
            f"  root cause {ok(truth['root_cause_ok'])}  culprit named {ok(truth['culprit_ok'])}  "
            f"fix {ok(truth['fix_ok'])}"
        )


demo_app = typer.Typer(
    help="The demo stack: a real containerised system to break and let the agent fix."
)
app.add_typer(demo_app, name="demo")

DEMO_ENV = {
    "ADAPTER_BACKEND": "live",
    "ACTUATOR": "demo",
    "REDIS_URL": "redis://localhost:6380/0",
    "LIVE_TOPOLOGY_FILE": "demo/state/topology.json",
    "LIVE_DEPLOY_LOG": "demo/state/deploys.json",
    "LIVE_FLAG_FILE": "demo/state/flags.json",
    "VERIFY_TIMEOUT_S": "300",
}


def _stack():
    from pager_zero.demo import DemoStack

    return DemoStack(settings.demo_dir, "redis://localhost:6380/0")


@demo_app.command("up")
def demo_up(
    no_reset: bool = typer.Option(False, "--no-reset", help="Keep demo/state as is."),
) -> None:
    """Build + start shop-api, worker, loadgen, Redis, Redpanda, Postgres, Prometheus, Alertmanager."""
    st = _stack()
    console.print("building and starting the demo stack (first time: ~1–2 min)…")
    st.up(reset=not no_reset)
    ok = st.wait_healthy()
    console.print(st.ps())
    console.print(
        "[green]shop-api healthy[/green]" if ok else "[red]shop-api not healthy yet[/red]"
    )
    console.print(
        "\nNow serve Pager-Zero against it (Alertmanager pages http://localhost:8000):\n"
        "  [bold]make demo-serve[/bold]      (= "
        + " ".join(f"{k}={v}" for k, v in DEMO_ENV.items())
        + " uv run pz serve)\n"
        "then break something:  [bold]uv run pz demo chaos bad-deploy[/bold]\n"
        "Prometheus http://localhost:9090 · Alertmanager http://localhost:9093 · shop-api http://localhost:8010"
    )


@demo_app.command("down")
def demo_down(volumes: bool = typer.Option(False, "-v", help="Also delete data volumes.")) -> None:
    """Stop the demo stack."""
    _stack().down(volumes=volumes)
    console.print("demo stack stopped")


@demo_app.command("status")
def demo_status() -> None:
    """Containers + current knobs (version, fault toggles)."""
    st = _stack()
    console.print(st.ps())
    console.print({k: v for k, v in st.read_env().items()})


@demo_app.command("chaos")
def demo_chaos(
    fault: str = typer.Argument(
        "random",
        help="random (sealed: you are not told which) | bad-deploy | flag-regression | cache-eviction | consumer-lag | downstream-outage",
    ),
) -> None:
    """Really break the running stack. Alertmanager will page `pz serve` within ~1–2 minutes."""
    from pager_zero.demo import FAULTS, RANDOM

    if fault == RANDOM:
        console.print(f"[bold red]chaos[/bold red] {_stack().chaos_random()}")
    elif fault not in FAULTS:
        console.print(f"[red]unknown fault {fault!r}[/red]; one of random, {', '.join(FAULTS)}")
        raise typer.Exit(code=2)
    else:
        console.print(f"[bold red]chaos[/bold red] {_stack().chaos(fault)}")
    console.print(
        "[dim]watch http://localhost:9090/alerts — the alert fires after the `for:` window, then the incident appears in the UI[/dim]"
    )


@demo_app.command("reveal")
def demo_reveal() -> None:
    """Show which fault `pz demo chaos random` injected (keeps it sealed for the incident card)."""
    doc = _stack().sealed()
    if not doc:
        console.print("nothing sealed")
        return
    console.print(f"[bold]{doc['fault']}[/bold] at {doc['at']}: {doc['detail']}")


@demo_app.command("heal")
def demo_heal() -> None:
    """Undo every fault (healthy version, default flags, 64mb cache, 3 fast workers, payments up)."""
    for line in _stack().heal():
        console.print(f"  ✓ {line}")


memory_app = typer.Typer(help="Incident memory (pgvector).")
app.add_typer(memory_app, name="memory")


@memory_app.command("init")
def memory_init() -> None:
    """Create the schema and load the seed runbooks."""
    from pager_zero.memory import get_store
    from pager_zero.memory.runbooks import RUNBOOKS

    store = get_store()
    existing = store.count()
    if existing < 0:
        console.print("[red]memory backend unreachable — is `docker compose up -d` running?[/red]")
        raise typer.Exit(code=1)
    if store.recall("runbook", k=1, kind="runbook"):
        console.print(f"runbooks already loaded ({existing} memories)")
        return
    for rb in RUNBOOKS:
        store.remember(rb)
    console.print(
        f"loaded {len(RUNBOOKS)} runbooks (backend={settings.memory_backend}, total={store.count()})"
    )


@memory_app.command("search")
def memory_search(query: str, k: int = typer.Option(3, "-k")) -> None:
    """Semantic search over remembered incidents and runbooks."""
    from pager_zero.memory import get_store

    for h in get_store().recall(query, k=k):
        console.print(f"[bold]{h.score:.3f}[/bold] [{h.kind}] {h.title}\n   {h.text[:200]}")


@memory_app.command("count")
def memory_count() -> None:
    from pager_zero.memory import get_store

    console.print(get_store().count())


@app.command("bench")
def bench(
    n: int = typer.Option(20, "--n"),
    seed: int = typer.Option(7, "--seed"),
    families: str = typer.Option(
        "", "--families", help="Comma-separated subset of scenario families."
    ),
    memory: bool = typer.Option(
        False,
        "--memory/--no-memory",
        help="Let the agent recall past incidents (off = fair, cold-start measurement).",
    ),
    pause: float = typer.Option(
        0.0, "--pause", help="Seconds to wait between incidents (free-tier rate limits)."
    ),
) -> None:
    """Run N synthetic incidents with auto-approval and score root-cause / fix accuracy."""
    import asyncio

    from pager_zero.bench.runner import run_bench

    fams = [f.strip() for f in families.split(",") if f.strip()] or None
    asyncio.run(run_bench(n, seed, fams, memory, console, pause_s=pause))


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the HTTP API (FastAPI). Docs at /docs."""
    import uvicorn

    uvicorn.run("pager_zero.api.app:app", host=host, port=port, reload=reload)


# convenience aliases at top level: `pz approve <id>` / `pz reject <id>`
app.command("approve", hidden=True)(incident_approve)
app.command("reject", hidden=True)(incident_reject)


if __name__ == "__main__":
    app()
