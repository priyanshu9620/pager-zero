import json
import sys
from pathlib import Path

import pytest

from pager_zero.mcp_server.server import synthetic_server
from pager_zero.synth import generate
from pager_zero.synth.store import load_world

READ_TOOLS = {
    "incident_context",
    "service_info",
    "query_metrics",
    "search_logs",
    "recent_deploys",
    "recent_commits",
    "get_diff",
    "get_flag",
    "flag_history",
    "consumer_lag",
    "lag_history",
    "cache_stats",
    "cache_history",
    "hot_keys",
    "partition_latency",
    "dependency_health",
}
MUTATING_TOOLS = {
    "rollback_deploy",
    "set_flag",
    "scale_consumers",
    "restart_consumer_group",
    "set_cache_limit",
    "rate_limit_key",
    "enable_fallback",
}


def _payload(result):
    """FastMCP.call_tool returns (content_blocks, structured); prefer the structured part."""
    if isinstance(result, tuple):
        blocks, structured = result
        if isinstance(structured, dict):
            return (
                structured.get("result", structured)
                if set(structured) == {"result"}
                else structured
            )
        text = "".join(getattr(b, "text", "") for b in blocks)
        return json.loads(text) if text else None
    return result


async def test_read_only_server_has_no_mutating_tools(tmp_path: Path) -> None:
    srv = synthetic_server("bad-deploy", 42, None, actions_enabled=False)
    names = {t.name for t in await srv.list_tools()}
    assert READ_TOOLS <= names
    assert not (MUTATING_TOOLS & names)
    for t in await srv.list_tools():
        assert t.annotations and t.annotations.readOnlyHint is True


async def test_actions_server_exposes_mutating_tools() -> None:
    srv = synthetic_server("bad-deploy", 42, None, actions_enabled=True)
    tools = {t.name: t for t in await srv.list_tools()}
    assert MUTATING_TOOLS <= set(tools)
    for n in MUTATING_TOOLS:
        assert tools[n].annotations.destructiveHint is True


async def test_investigation_trail_bad_deploy() -> None:
    srv = synthetic_server("bad-deploy", 42, None, actions_enabled=False)
    ctx = _payload(await srv.call_tool("incident_context", {}))
    svc = ctx["alert"]["service"]
    deploys = _payload(await srv.call_tool("recent_deploys", {"service": svc, "minutes": 60}))
    assert len(deploys) == 1 and deploys[0]["version"] == "v1.60"
    p99 = _payload(
        await srv.call_tool("query_metrics", {"metric": "http_p99_ms", "labels": {"service": svc}})
    )
    assert any(s["last"] > 3 * s["first"] for s in p99)
    logs = _payload(await srv.call_tool("search_logs", {"service": svc, "query": "error"}))
    assert logs["total_matches"] > 100 and "slow query" in logs["top_messages"][0]["message"]
    diff = _payload(await srv.call_tool("get_diff", {"sha": deploys[0]["commit_sha"]}))
    assert "for _, id := range ids" in diff


async def test_mutation_persists_state_across_processes(tmp_path: Path) -> None:
    state = tmp_path / "world.pkl"
    w = generate("hot-partition", 3)
    gt = w.ground_truth
    srv = synthetic_server("hot-partition", 3, state, actions_enabled=True)
    before = _payload(await srv.call_tool("partition_latency", {"table": gt.fix_target["table"]}))
    res = _payload(await srv.call_tool("rate_limit_key", dict(gt.fix_target)))
    assert res["ok"] and "recovering" in res["detail"]
    # a brand-new server over the same state file sees the healed world
    srv2 = synthetic_server("hot-partition", 3, state, actions_enabled=False)
    after = _payload(await srv2.call_tool("partition_latency", {"table": gt.fix_target["table"]}))
    hot_before = max(s["last"] for s in before)
    hot_after = max(s["last"] for s in after)
    assert hot_after < hot_before / 5
    assert load_world(state).resolved


@pytest.mark.skipif(sys.platform == "win32", reason="stdio spawn")
async def test_end_to_end_over_stdio_with_langchain_adapter() -> None:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "pz": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "pager_zero.cli",
                    "mcp",
                    "serve",
                    "--scenario",
                    "consumer-lag",
                    "--seed",
                    "7",
                ],
            }
        }
    )
    tools = await client.get_tools()
    names = {t.name for t in tools}
    assert "incident_context" in names and "scale_consumers" not in names
    ctx = next(t for t in tools if t.name == "incident_context")
    out = await ctx.ainvoke({})
    assert "ConsumerLagHigh" in str(out)
