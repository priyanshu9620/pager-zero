"""hot-partition: one key suddenly takes ~40% of a table's traffic (a campaign started);
the shard owning it saturates, so p99 degrades for the subset of requests that hit it while
service-wide averages look only mildly off.

Correct fix: rate_limit_key(table, key, rps <= 500).  No deploy, no flag, no lag.
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.adapters.base import HotKey
from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World


def build(seed: int) -> World:
    b = WorldBuilder("hot-partition", seed)
    service = str(b.pick(["offer-service", "inventory-service"]))
    table = str(b.pick(b.services[service].tables))
    t = b.tables[table]
    hot_shard = b.rng.randint(0, len(t.partition_latency) - 1)
    hot_key = f"{table.split('_by_')[-1]}:{b.rng.randint(10000, 99999)}"
    campaign = f"campaign-{b.sha('campaign')[:4]}"

    base_lat = t.partition_latency[hot_shard].last() or 6
    b.metrics.remove(t.partition_latency[hot_shard])
    hot = b.series(
        "partition_p99_ms",
        {"table": table, "shard": str(hot_shard)},
        b.step(base_lat, base_lat * 60),
    )
    t.partition_latency[hot_shard] = hot
    b.recovery.append((hot, base_lat))
    t.hot_keys = [HotKey(hot_key, 0.42)] + [
        HotKey(h.key, round(h.share * 0.6, 3)) for h in t.hot_keys[:4]
    ]

    p99 = next(
        s for s in b.metrics if s.metric == "http_p99_ms" and s.labels == {"service": service}
    )
    b.metrics.remove(p99)
    s = b.series("http_p99_ms", p99.labels, b.step(p99.last() or 200, (p99.last() or 200) * 4.1))
    b.recovery.append((s, p99.last() or 200))
    rps = next(s for s in b.metrics if s.metric == "http_rps" and s.labels == {"service": service})
    b.metrics.remove(rps)
    b.series("http_rps", rps.labels, b.step(rps.last() or 1000, (rps.last() or 1000) * 1.15))

    b.log(
        "checkout-api",
        "INFO",
        f"{campaign} went live: homepage banner → product {hot_key.split(':')[-1]}",
        b.t0 - timedelta(seconds=30),
    )
    b.burst_logs(
        service,
        "WARN",
        f"db: coordinator timeout on {table} partition key={hot_key} (shard {hot_shard})",
        b.t0 + timedelta(seconds=45),
        per_minute=30,
    )
    b.burst_logs(
        service,
        "WARN",
        f"db: shard {hot_shard} load 94% (others < 40%)",
        b.t0 + timedelta(minutes=1),
        per_minute=4,
    )

    alert = b.alert(service, "HighLatencyP99", f"{service} p99 > 800ms for 2m")
    truth = GroundTruth(
        family="hot-partition",
        cause=f"Key {hot_key} on {table} took ~42% of traffic after {campaign} launched; shard {hot_shard} saturated",
        culprit={
            "service": service,
            "table": table,
            "key": hot_key,
            "shard": hot_shard,
            "trigger": campaign,
        },
        fix_action="rate_limit_key",
        fix_target={"table": table, "key": hot_key, "rps": 500},
    )
    return b.finish(alert, truth)
