from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from pager_zero.agent.runner import new_incident, resume, run_until_approval
from pager_zero.bench.scoring import Score, score
from pager_zero.config import settings
from pager_zero.errors import root_cause
from pager_zero.synth import generate, list_scenarios

RESULTS_DIR = Path("bench/results")


def plan_incidents(n: int, seed: int, families: list[str] | None) -> list[tuple[str, int]]:
    fams = families or list_scenarios()
    return [(fams[i % len(fams)], seed * 1000 + i) for i in range(n)]


async def run_one(scenario: str, inc_seed: int, memory: bool) -> Score:
    quiet = Console(quiet=True)
    meta = new_incident(scenario, inc_seed, memory=memory)
    gt = generate(scenario, inc_seed).ground_truth  # fresh copy; the agent's world is separate
    t0 = time.time()
    try:
        state = await run_until_approval(meta, quiet)
        if state.get("status") == "awaiting_approval":
            state = await resume(meta, {"decision": "approve", "note": "bench auto-approve"}, quiet)
        return score(scenario, inc_seed, meta, gt, state, time.time() - t0)
    except Exception as e:  # noqa: BLE001 — a crash is a failed incident, not a failed bench
        err = f"{type(e).__name__}: {str(root_cause(e))[:300]}"
        return score(
            scenario,
            inc_seed,
            meta,
            gt,
            {"status": "error"},
            time.time() - t0,
            error=err or traceback.format_exc()[-300:],
        )


async def run_bench(
    n: int,
    seed: int,
    families: list[str] | None,
    memory: bool,
    console: Console,
    pause_s: float = 0.0,
) -> dict[str, Any]:
    plan = plan_incidents(n, seed, families)
    console.print(
        f"bench: {n} incidents, seed {seed}, model {settings.llm_provider}/{settings.llm_model}, memory={'on' if memory else 'off'}"
    )
    scores: list[Score] = []
    consecutive_errors = 0
    for i, (scenario, inc_seed) in enumerate(plan, 1):
        s = await run_one(scenario, inc_seed, memory)
        scores.append(s)
        consecutive_errors = consecutive_errors + 1 if s.error else 0
        if consecutive_errors >= 3:
            console.print(
                f"[red]aborting: 3 consecutive infrastructure errors (last: {s.error}). "
                "Results are NOT written — fix the cause (quota? docker?) and re-run.[/red]"
            )
            return {"aborted": True, "incidents": [x.as_dict() for x in scores]}
        mark = lambda b: "[green]✔[/green]" if b else "[red]✘[/red]"  # noqa: E731
        console.print(
            f"{i:>3}/{n} {scenario:<18} seed={inc_seed:<6} rca {mark(s.root_cause_ok)} culprit {mark(s.culprit_ok)} "
            f"fix {mark(s.fix_ok)} verified {mark(s.verified)}  steps={s.steps:<2} {s.duration_s:>6.1f}s"
            + (f"  [red]{s.error}[/red]" if s.error else "")
        )
        if pause_s and i < n:
            await asyncio.sleep(pause_s)
    result = summarise(scores, seed, memory)
    path = write_results(result)
    render(result, console)
    console.print(f"results → {path}")
    return result


def summarise(scores: list[Score], seed: int, memory: bool) -> dict[str, Any]:
    by_fam: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        by_fam[s.scenario].append(s)

    def agg(xs: list[Score]) -> dict[str, Any]:
        n = len(xs)
        return {
            "n": n,
            "root_cause_ok": sum(x.root_cause_ok for x in xs),
            "culprit_ok": sum(x.culprit_ok for x in xs),
            "fix_ok": sum(x.fix_ok for x in xs),
            "verified": sum(x.verified for x in xs),
            "errors": sum(1 for x in xs if x.error),
            "avg_steps": round(sum(x.steps for x in xs) / n, 1) if n else 0,
            "avg_duration_s": round(sum(x.duration_s for x in xs) / n, 1) if n else 0,
            "avg_input_tokens": int(sum(x.input_tokens for x in xs) / n) if n else 0,
            "avg_output_tokens": int(sum(x.output_tokens for x in xs) / n) if n else 0,
        }

    return {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "seed": seed,
        "memory": memory,
        "max_steps": settings.max_steps,
        "total": agg(scores),
        "by_family": {f: agg(xs) for f, xs in sorted(by_fam.items())},
        "incidents": [s.as_dict() for s in scores],
    }


def write_results(result: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = result["ran_at"].replace(":", "").replace("-", "")[:15]
    model = result["model"].replace("/", "-")
    path = RESULTS_DIR / f"{result['provider']}__{model}__{stamp}.json"
    path.write_text(json.dumps(result, indent=2))
    return path


def render(result: dict[str, Any], console: Console) -> None:
    t = Table(
        title=f"pager-zero bench — {result['provider']}/{result['model']} (seed {result['seed']}, memory {'on' if result['memory'] else 'off'})"
    )
    for col in (
        "family",
        "n",
        "root cause",
        "culprit named",
        "fix",
        "verified",
        "avg steps",
        "avg s",
        "avg tok in/out",
    ):
        t.add_column(col, justify="right" if col != "family" else "left")

    def row(name: str, a: dict[str, Any], bold: bool = False) -> None:
        n = a["n"] or 1
        cells = [
            name,
            str(a["n"]),
            f"{a['root_cause_ok']} ({a['root_cause_ok'] / n:.0%})",
            f"{a['culprit_ok']} ({a['culprit_ok'] / n:.0%})",
            f"{a['fix_ok']} ({a['fix_ok'] / n:.0%})",
            f"{a['verified']} ({a['verified'] / n:.0%})",
            str(a["avg_steps"]),
            str(a["avg_duration_s"]),
            f"{a['avg_input_tokens']}/{a['avg_output_tokens']}",
        ]
        t.add_row(*([f"[bold]{c}[/bold]" for c in cells] if bold else cells))

    for fam, a in result["by_family"].items():
        row(fam, a)
    row("TOTAL", result["total"], bold=True)
    console.print(t)
