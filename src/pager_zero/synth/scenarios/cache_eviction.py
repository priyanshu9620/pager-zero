"""cache-eviction: a cache tier hits its memory cap; evictions explode, hit-rate collapses,
the database behind it saturates and service latency follows.

Trigger: a catalog refresh roughly doubled key count (logged by the owning service).
Correct fix: set_cache_limit(cache, >= 2x current max).  No deploy, no flag change.
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World


def build(seed: int) -> World:
    b = WorldBuilder("cache-eviction", seed)
    service, cache = str(b.pick(["offer-service", "pricing-service"])), ""
    cache = b.services[service].caches[0]
    c = b.caches[cache]
    fill_at = b.t0 - timedelta(minutes=3)

    base_used = c.used_mb_series.last() or c.max_memory_mb * 0.5
    base_hit = c.hit_rate_series.last() or 0.96
    b.metrics[:] = [
        s for s in b.metrics if s not in (c.used_mb_series, c.hit_rate_series, c.evictions_series)
    ]
    c.used_mb_series = b.series(
        "cache_used_mb",
        {"cache": cache, "service": service},
        b.step(base_used, c.max_memory_mb * 0.999, at=fill_at, ramp_s=180),
        noise=0.002,
    )
    c.hit_rate_series = b.series(
        "cache_hit_rate", {"cache": cache, "service": service}, b.step(base_hit, 0.38, ramp_s=120)
    )
    c.evictions_series = b.series(
        "cache_evictions_per_s",
        {"cache": cache, "service": service},
        b.step(0.5, 18_000, ramp_s=60),
    )
    c.keys = int(c.keys * 2.1)
    b.recovery += [(c.hit_rate_series, base_hit), (c.evictions_series, 0.5)]

    qps = next(s for s in b.metrics if s.metric == "db_qps" and s.labels == {"service": service})
    b.metrics.remove(qps)
    s = b.series("db_qps", qps.labels, b.step(qps.last() or 1000, (qps.last() or 1000) * 7))
    b.recovery.append((s, qps.last() or 1000))
    p99 = next(
        s for s in b.metrics if s.metric == "http_p99_ms" and s.labels == {"service": service}
    )
    b.metrics.remove(p99)
    s = b.series("http_p99_ms", p99.labels, b.step(p99.last() or 200, (p99.last() or 200) * 4.8))
    b.recovery.append((s, p99.last() or 200))

    b.log(
        service,
        "INFO",
        f"catalog refresh complete: {c.keys:,} entries written to {cache}",
        fill_at + timedelta(minutes=2),
    )
    b.burst_logs(
        service,
        "WARN",
        f"{cache}: evicting under memory pressure (maxmemory {c.max_memory_mb}MB reached)",
        b.t0,
        per_minute=25,
    )
    b.burst_logs(
        service,
        "WARN",
        "db: read latency elevated, pool near capacity",
        b.t0 + timedelta(minutes=1),
        per_minute=10,
    )

    alert = b.alert(service, "HighLatencyP99", f"{service} p99 > 800ms for 2m")
    truth = GroundTruth(
        family="cache-eviction",
        cause=f"{cache} hit its {c.max_memory_mb}MB maxmemory after the catalog refresh doubled key count; eviction storm dropped hit-rate to ~40% and pushed reads to the DB",
        culprit={
            "service": service,
            "cache": cache,
            "max_memory_mb": c.max_memory_mb,
            "keys_now": c.keys,
        },
        fix_action="set_cache_limit",
        fix_target={"name": cache, "max_memory_mb": c.max_memory_mb * 2},
    )
    return b.finish(alert, truth)
