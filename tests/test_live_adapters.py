"""Live adapters against mocked transports / fake clients — no network, no Docker."""

from __future__ import annotations

import json
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from pager_zero.adapters import base
from pager_zero.adapters.live import (
    DryRunActuator,
    FileDeployLog,
    FileFlagStore,
    GitHubSCM,
    KafkaMessaging,
    PrometheusMetrics,
    RedisCache,
    StaticTopology,
)

NOW = datetime(2026, 11, 8, 11, 20, tzinfo=UTC)
START = NOW - timedelta(minutes=30)


def _prom(handler):
    return PrometheusMetrics(
        "http://prom:9090", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_prometheus_query_range_parses_series():
    def handler(req: httpx.Request):
        assert req.url.path == "/api/v1/query_range"
        assert req.url.params["query"] == 'http_p99_ms{service="checkout-api"}'
        body = {
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "http_p99_ms", "service": "checkout-api"},
                        "values": [[START.timestamp(), "250"], [NOW.timestamp(), "1100"]],
                    }
                ]
            }
        }
        return httpx.Response(200, json=body)

    series = _prom(handler).query_range("http_p99_ms", {"service": "checkout-api"}, START, NOW)
    assert len(series) == 1 and series[0].labels == {"service": "checkout-api"}
    assert series[0].points[0][1] == 250 and series[0].last() == 1100
    assert isinstance(series[0], base.Series)


def test_github_commits_and_diff():
    def handler(req: httpx.Request):
        if req.url.path == "/repos/acme/checkout/commits":
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "df705b8abcdef",
                        "commit": {
                            "message": "feat: breakdown\n\nbody",
                            "author": {"name": "dev.k", "date": "2026-11-08T10:32:04Z"},
                        },
                    }
                ],
            )
        if req.url.path == "/repos/acme/checkout/commits/df705b8":
            assert req.headers["accept"] == "application/vnd.github.diff"
            return httpx.Response(200, text="--- a/x\n+++ b/x\n")
        return httpx.Response(404)

    scm = GitHubSCM(
        "tok",
        {"checkout-api": "acme/checkout"},
        client=httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        ),
    )
    commits = scm.recent_commits("checkout-api", START)
    assert commits[0].sha == "df705b8" and commits[0].message == "feat: breakdown"
    assert scm.diff("df705b8").startswith("--- a/x")
    assert scm.recent_commits("unknown-service", START) == []


def test_kafka_lag_from_offsets():
    TP = namedtuple("TP", "topic partition")
    OM = namedtuple("OM", "offset")

    class FakeAdmin:
        def list_group_offsets(self, specs):
            return {
                "order-events-consumer": {
                    TP("order-events", 0): OM(100),
                    TP("order-events", 1): OM(90),
                }
            }

        def describe_groups(self, groups):
            return {"order-events-consumer": {"members": [1, 2, 3]}}

    class FakeConsumer:
        def end_offsets(self, tps):
            return {tp: 150 for tp in tps}

    topo = StaticTopology(Path("live/topology.example.json"), prom=None)  # type: ignore[arg-type]
    k = KafkaMessaging("b:9092", topo, prom=None, admin=FakeAdmin(), consumer_factory=FakeConsumer)  # type: ignore[arg-type]
    lag = k.consumer_lag("order-events-consumer")
    assert (
        lag.per_partition == {0: 50, 1: 60}
        and lag.total == 110
        and lag.members == 3
        and lag.topic == "order-events"
    )
    assert k.list_groups("order-service") == ["order-events-consumer"]


def test_redis_snapshot_from_info():
    class FakeRedis:
        def info(self):
            return {
                "maxmemory": 8 * 1024**3,
                "used_memory": 7.9 * 1024**3,
                "keyspace_hits": 400,
                "keyspace_misses": 600,
                "db0": {"keys": 1234},
            }

    topo = StaticTopology(Path("live/topology.example.json"), prom=None)  # type: ignore[arg-type]
    snap = RedisCache("redis://x", topo, prom=None, client=FakeRedis()).snapshot(
        "checkout-session-cache"
    )  # type: ignore[arg-type]
    assert (
        snap.max_memory_mb == 8192
        and round(snap.used_memory_mb) == 8090
        and snap.hit_rate == 0.4
        and snap.keys == 1234
    )
    assert isinstance(snap, base.CacheSnapshot)


def test_file_deploys_flags_and_dry_run_actuator(tmp_path: Path):
    deploys = FileDeployLog(Path("live/deploys.example.json"))
    assert deploys.current_version("checkout-api") == "v1.60"
    assert deploys.recent_deploys("checkout-api", START)[0].commit_sha == "df705b8"
    assert deploys.recent_deploys("order-service", START) == []

    flags_path = tmp_path / "flags.json"
    flags_path.write_text(Path("live/flags.example.json").read_text())
    topo = StaticTopology(Path("live/topology.example.json"), prom=None)  # type: ignore[arg-type]
    flags = FileFlagStore(flags_path, topo)
    assert flags.get("orders.batch_size") == 100
    assert flags.flag_history("orders.batch_size", START)[0].new == 1
    assert "orders.batch_size" in flags.list_keys("order-service")

    dry = DryRunActuator(flags, deploys, dry_run=True)
    res = dry.set_flag("orders.batch_size", 50)
    assert (
        res.ok and res.detail.startswith("DRY-RUN") and flags.get("orders.batch_size") == 100
    )  # unchanged
    wet = DryRunActuator(flags, deploys, dry_run=False)
    wet.set_flag("orders.batch_size", 50)
    assert (
        flags.get("orders.batch_size") == 50
        and json.loads(flags_path.read_text())["history"][-1]["actor"] == "pager-zero"
    )


def test_live_classes_satisfy_protocols():
    topo = StaticTopology(Path("live/topology.example.json"), prom=None)  # type: ignore[arg-type]
    assert isinstance(
        _prom(lambda r: httpx.Response(200, json={"data": {"result": []}})), base.MetricsReader
    )
    assert isinstance(GitHubSCM(None, {}), base.SCMReader)
    assert isinstance(FileDeployLog(Path("x")), base.DeployReader)
    assert isinstance(FileFlagStore(Path("x"), topo), base.FlagReader)
    assert isinstance(topo, base.TopologyReader)
    assert isinstance(DryRunActuator(), base.Actuator)
    assert isinstance(KafkaMessaging("b", topo, None), base.MessagingReader)  # type: ignore[arg-type]
    assert isinstance(RedisCache("r", topo, None), base.CacheReader)  # type: ignore[arg-type]


def test_live_incident_does_not_pin_the_clock():
    """P-31: a pinned `now` froze every live query window at MCP-server start."""
    from datetime import UTC, datetime, timedelta

    from pager_zero.adapters.live import live_incident
    from pager_zero.mcp_server.server import build_server

    inc = live_incident("shop-api", "HighLatencyP99", "s")
    assert "now" not in inc and inc["alert"]["service"] == "shop-api"

    class Topo:
        def services(self):
            return ["shop-api"]

    class A:
        topology = Topo()

    srv = build_server(A(), actions_enabled=False, incident=inc)
    import asyncio

    out = asyncio.run(srv.call_tool("incident_context", {}))
    ctx = out[1] if isinstance(out, tuple) else out
    reported = datetime.fromisoformat(ctx["now"])
    assert abs(datetime.now(UTC) - reported) < timedelta(seconds=5)
