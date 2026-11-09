from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from pager_zero.adapters.base import Adapters, Series

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
MUTATE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

INSTRUCTIONS = """You are connected to Pager-Zero's incident tools for one fictional e-commerce platform.
Start with `incident_context`. Investigate with the read-only tools: correlate the alert's onset
with deploys, flag changes, consumer lag, cache health, hot partitions and dependency health.
Cite the tool outputs you relied on. Mutating tools, if present, are gated by human approval."""


# ------------------------------------------------------------------ serialisation helpers


def _j(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _j(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, dict):
        return {str(k): _j(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_j(v) for v in obj]
    return obj


def _series_summary(s: Series, max_points: int = 16) -> dict[str, Any]:
    """LLM-friendly view of a time series: stats + a change point + a downsampled trace."""
    if not s.points:
        return {"metric": s.metric, "labels": s.labels, "points": 0}
    vals = [v for _, v in s.points]
    first, last = vals[0], vals[-1]
    change_at = None
    base = sum(vals[: max(1, len(vals) // 6)]) / max(1, len(vals) // 6)
    for t, v in s.points:
        if base and abs(v - base) / abs(base) > 0.3:
            change_at = t
            break
    step = max(1, len(s.points) // max_points)
    trace = [(t.strftime("%H:%M"), v) for t, v in s.points[::step]]
    if s.points[-1] not in s.points[::step]:
        trace.append((s.points[-1][0].strftime("%H:%M"), last))
    return {
        "metric": s.metric,
        "labels": s.labels,
        "points": len(vals),
        "first": first,
        "min": min(vals),
        "max": max(vals),
        "last": last,
        "change_vs_start": f"{(last - first) / first * 100:+.0f}%" if first else None,
        "first_deviation_at": change_at.isoformat(timespec="seconds") if change_at else None,
        "trace": trace,
    }


# ------------------------------------------------------------------ server factory


def build_server(
    adapters: Adapters,
    *,
    actions_enabled: bool = False,
    on_mutation: Any = None,
    incident: dict[str, Any] | None = None,
) -> FastMCP:
    """Create the MCP server.

    `incident` is what `incident_context` returns (alert, now, window). For the synthetic backend
    it comes from the World; for live backends from the triggering alert payload.
    `on_mutation()` is called after every successful mutating tool (used to persist state).
    """
    mcp = FastMCP("pager-zero", instructions=INSTRUCTIONS)
    A = adapters
    inc = incident or {}

    def now() -> datetime:
        return datetime.fromisoformat(inc["now"]) if "now" in inc else datetime.now().astimezone()

    def since(minutes: int) -> tuple[datetime, datetime]:
        n = now()
        return n - timedelta(minutes=minutes), n

    # ---------------- orientation

    @mcp.tool(annotations=READ)
    def incident_context() -> dict[str, Any]:
        """The alert that fired, the current time, the observation window, and the services that exist. Call this first."""
        return {
            **inc,
            "services": A.topology.services(),
            "hint": "Correlate the alert onset with deploys, flag changes, lag, cache and dependency health. Absence of a change is evidence too.",
        }

    @mcp.tool(annotations=READ)
    def service_info(service: str) -> dict[str, Any]:
        """Topology for one service: current version, replicas, dependencies, consumer groups, caches, tables, flags."""
        return {
            "service": service,
            "version": A.deploys.current_version(service),
            "dependencies": A.topology.dependencies(service),
            "consumer_groups": A.messaging.list_groups(service),
            "caches": A.cache.list_caches(service),
            "tables": A.datastore.list_tables(service),
            "flags": A.flags.list_keys(service),
            "metrics_available": A.metrics.list_metrics(service),
        }

    # ---------------- metrics & logs

    @mcp.tool(annotations=READ)
    def query_metrics(
        metric: str, labels: dict[str, str] | None = None, minutes: int = 30
    ) -> list[dict[str, Any]]:
        """Time series for a metric over the last N minutes, filtered by labels (e.g. {"service": "checkout-api"}). Returns stats, first deviation time and a downsampled trace per matching series."""
        start, end = since(minutes)
        return [_series_summary(s) for s in A.metrics.query_range(metric, labels or {}, start, end)]

    @mcp.tool(annotations=READ)
    def search_logs(
        service: str, query: str = "", minutes: int = 30, limit: int = 20
    ) -> dict[str, Any]:
        """Search a service's logs over the last N minutes. `query` matches message text or a level (error/warn). Returns the most frequent messages and a sample of recent lines."""
        start, end = since(minutes)
        lines = A.logs.search(service, query, start, end, limit=10_000)
        top = Counter(line.message for line in lines).most_common(8)
        first_seen = {}
        for line in lines:
            first_seen.setdefault(line.message, line.ts)
        return {
            "total_matches": len(lines),
            "top_messages": [
                {
                    "count": c,
                    "message": m,
                    "first_seen": first_seen[m].isoformat(timespec="seconds"),
                }
                for m, c in top
            ],
            "sample": _j(lines[-limit:]),
        }

    # ---------------- change sources

    @mcp.tool(annotations=READ)
    def recent_deploys(service: str, minutes: int = 180) -> list[dict[str, Any]]:
        """Deploys of a service in the last N minutes (version, previous version, time, commit)."""
        start, _ = since(minutes)
        return _j(A.deploys.recent_deploys(service, start))

    @mcp.tool(annotations=READ)
    def recent_commits(service: str, hours: int = 24) -> list[dict[str, Any]]:
        """Commits to a service's repo in the last N hours (sha, message, files). Use get_diff for the code."""
        start = now() - timedelta(hours=hours)
        return [
            {k: v for k, v in _j(c).items() if k != "diff"}
            for c in A.scm.recent_commits(service, start)
        ]

    @mcp.tool(annotations=READ)
    def get_diff(sha: str) -> str:
        """The unified diff of one commit."""
        return A.scm.diff(sha)

    @mcp.tool(annotations=READ)
    def get_flag(key: str) -> dict[str, Any]:
        """Current value of a dynamic config flag."""
        return {"key": key, "value": A.flags.get(key)}

    @mcp.tool(annotations=READ)
    def flag_history(key: str, hours: int = 24) -> list[dict[str, Any]]:
        """Changes to a flag in the last N hours (old → new, when, by whom)."""
        return _j(A.flags.flag_history(key, now() - timedelta(hours=hours)))

    # ---------------- messaging

    @mcp.tool(annotations=READ)
    def consumer_lag(group: str) -> dict[str, Any]:
        """Current lag of a consumer group: members, per-partition lag, total."""
        lag = A.messaging.consumer_lag(group)
        return {**_j(lag), "total": lag.total}

    @mcp.tool(annotations=READ)
    def lag_history(group: str, minutes: int = 30) -> dict[str, Any]:
        """Consumer-group total lag over the last N minutes."""
        start, end = since(minutes)
        return _series_summary(A.messaging.lag_history(group, start, end))

    # ---------------- cache

    @mcp.tool(annotations=READ)
    def cache_stats(name: str) -> dict[str, Any]:
        """Current snapshot of a cache: max memory, used, hit rate, evictions/s, key count."""
        return _j(A.cache.snapshot(name))

    @mcp.tool(annotations=READ)
    def cache_history(name: str, metric: str = "hit_rate", minutes: int = 30) -> dict[str, Any]:
        """Cache metric over time. metric ∈ {hit_rate, used_mb, evictions}."""
        start, end = since(minutes)
        return _series_summary(A.cache.cache_history(name, metric, start, end))

    # ---------------- datastore

    @mcp.tool(annotations=READ)
    def hot_keys(table: str, top: int = 5) -> list[dict[str, Any]]:
        """Keys taking the largest share of a table's traffic right now."""
        return _j(A.datastore.hot_keys(table, top))

    @mcp.tool(annotations=READ)
    def partition_latency(table: str, minutes: int = 30) -> list[dict[str, Any]]:
        """p99 latency per shard/partition of a table over the last N minutes."""
        start, end = since(minutes)
        return [_series_summary(s) for s in A.datastore.partition_latency(table, start, end)]

    # ---------------- dependencies

    @mcp.tool(annotations=READ)
    def dependency_health(service: str, minutes: int = 30) -> dict[str, Any]:
        """Error rate of each dependency as seen from this service over the last N minutes."""
        start, end = since(minutes)
        return {
            d: _series_summary(s)
            for d, s in A.topology.dependency_health(service, start, end).items()
        }

    # ---------------- mutating tools (only exist when actions are enabled)

    if actions_enabled:

        def done(res: Any) -> dict[str, Any]:
            if on_mutation:
                on_mutation()
            return _j(res)

        @mcp.tool(annotations=MUTATE)
        def rollback_deploy(service: str, to_version: str) -> dict[str, Any]:
            """Roll a service back to a previous version."""
            return done(A.actuator.rollback_deploy(service, to_version))

        @mcp.tool(annotations=MUTATE)
        def set_flag(key: str, value: Any) -> dict[str, Any]:
            """Set a dynamic config flag to a value (used to revert a bad change)."""
            return done(A.actuator.set_flag(key, value))

        @mcp.tool(annotations=MUTATE)
        def scale_consumers(group: str, members: int) -> dict[str, Any]:
            """Scale a consumer group to N members."""
            return done(A.actuator.scale_consumers(group, members))

        @mcp.tool(annotations=MUTATE)
        def restart_consumer_group(group: str) -> dict[str, Any]:
            """Rolling-restart a consumer group."""
            return done(A.actuator.restart_consumer_group(group))

        @mcp.tool(annotations=MUTATE)
        def set_cache_limit(name: str, max_memory_mb: int) -> dict[str, Any]:
            """Raise/lower a cache's maxmemory."""
            return done(A.actuator.set_cache_limit(name, max_memory_mb))

        @mcp.tool(annotations=MUTATE)
        def rate_limit_key(table: str, key: str, rps: int) -> dict[str, Any]:
            """Rate-limit traffic to one hot partition key."""
            return done(A.actuator.rate_limit_key(table, key, rps))

        @mcp.tool(annotations=MUTATE)
        def enable_fallback(service: str, dependency: str) -> dict[str, Any]:
            """Enable graceful-degradation fallback for a failing dependency."""
            return done(A.actuator.enable_fallback(service, dependency))

    return mcp


# ------------------------------------------------------------------ synthetic wiring


def synthetic_server(
    scenario: str, seed: int, state: Path | None, actions_enabled: bool
) -> FastMCP:
    """Server over a synthetic World, persisted at `state` so other processes see mutations."""
    from pager_zero.adapters.synthetic import SyntheticAdapters
    from pager_zero.synth import generate
    from pager_zero.synth.store import load_world, save_world

    world = load_world(state) if state and state.exists() else generate(scenario, seed)
    if state and not state.exists():
        save_world(world, state)
    adapters = SyntheticAdapters.from_settings(world=world)
    incident = {
        "incident_id": world.alert.id,
        "alert": _j(world.alert),
        "now": world.now.isoformat(timespec="seconds"),
        "window_minutes": int(world.window.total_seconds() // 60),
    }
    return build_server(
        adapters,
        actions_enabled=actions_enabled,
        on_mutation=(lambda: save_world(world, state)) if state else None,
        incident=incident,
    )
