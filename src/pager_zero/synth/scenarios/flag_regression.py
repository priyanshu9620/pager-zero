"""flag-regression: someone flipped a dynamic config flag; no deploy happened.

Variants:
  ttl-zero       offers.cache.ttl_seconds 600 → 0  : cache hit-rate collapses, db_qps up, p99 up
  fallback-off   checkout.fallback_pricing.enabled → False : error rate up when pricing hiccups
  batch-one      orders.batch_size 100 → 1 : consumer throughput drops, lag grows
Correct fix: set_flag(key, old_value).  The absence of a deploy is itself evidence.
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World


def _replace(
    b: WorldBuilder, metric: str, labels: dict[str, str], fn, baseline: float | None = None
):
    b.metrics[:] = [s for s in b.metrics if not (s.metric == metric and s.labels == labels)]
    s = b.series(metric, labels, fn)
    if baseline is not None:
        b.recovery.append((s, baseline))
    return s


def build(seed: int) -> World:
    b = WorldBuilder("flag-regression", seed)
    variant = str(b.pick(["ttl-zero", "fallback-off", "batch-one"]))
    actor = str(b.pick(["flag-console:asha.r", "flag-console:rohan.t", "ops-bot"]))

    if variant == "ttl-zero":
        service, key, old, new = "offer-service", "offers.cache.ttl_seconds", 600, 0
        b.flip_flag(key, new, b.t0, actor)
        cache = "offer-cache"
        hit = b.caches[cache].hit_rate_series
        _replace(b, "cache_hit_rate", hit.labels, b.step(hit.last() or 0.96, 0.03), hit.last())
        b.caches[cache].hit_rate_series = next(
            s for s in b.metrics if s.metric == "cache_hit_rate" and s.labels == hit.labels
        )
        qps = next(
            s for s in b.metrics if s.metric == "db_qps" and s.labels == {"service": service}
        )
        _replace(
            b,
            "db_qps",
            qps.labels,
            b.step(qps.last() or 1000, (qps.last() or 1000) * 9),
            qps.last(),
        )
        p99 = next(
            s for s in b.metrics if s.metric == "http_p99_ms" and s.labels == {"service": service}
        )
        _replace(
            b,
            "http_p99_ms",
            p99.labels,
            b.step(p99.last() or 200, (p99.last() or 200) * 5),
            p99.last(),
        )
        b.burst_logs(
            service, "WARN", "cache miss storm: ttl=0, bypassing offer-cache", b.t0, per_minute=40
        )
        alert = b.alert(service, "HighLatencyP99", f"{service} p99 > 800ms for 2m")
    elif variant == "fallback-off":
        service, key, old, new = "checkout-api", "checkout.fallback_pricing.enabled", True, False
        b.flip_flag(key, new, b.t0, actor)
        # pricing has a mild, pre-existing hiccup that the fallback used to absorb
        dep = b.dep_health[service]["pricing-service"]
        _replace(
            b,
            "dependency_error_rate",
            dep.labels,
            b.step(0.001, 0.04, at=b.start + timedelta(minutes=5)),
        )
        err = next(
            s
            for s in b.metrics
            if s.metric == "http_error_rate" and s.labels == {"service": service}
        )
        _replace(b, "http_error_rate", err.labels, b.step(0.002, 0.041), 0.002)
        b.burst_logs(
            service,
            "ERROR",
            "pricing lookup failed and fallback_pricing disabled → 502",
            b.t0,
            per_minute=30,
        )
        alert = b.alert(service, "HighErrorRate", f"{service} 5xx rate > 2% for 2m")
    else:
        service, key, old, new = "order-service", "orders.batch_size", 100, 1
        b.flip_flag(key, new, b.t0, actor)
        group = "order-events-consumer"
        g = b.groups[group]
        rps = next(
            s for s in b.metrics if s.metric == "consumer_rps" and s.labels["group"] == group
        )
        _replace(
            b,
            "consumer_rps",
            rps.labels,
            b.step(rps.last() or 900, (rps.last() or 900) * 0.08),
            rps.last(),
        )
        lag = _replace(
            b,
            "consumer_lag",
            g.lag_series.labels,
            b.ramp(g.lag_series.last() or 100, 2500),
            g.lag_series.last(),
        )
        g.lag_series = lag
        g.per_partition_now = {
            p: int((lag.last() or 0) // g.partitions) for p in range(g.partitions)
        }
        b.burst_logs(
            service, "INFO", "processed batch of 1 message (batch_size=1)", b.t0, per_minute=50
        )
        alert = b.alert(service, "ConsumerLagHigh", f"{group} lag > 10k and growing", group=group)

    # an unrelated flag flip elsewhere, long before, as noise
    b.flip_flag(
        "inventory.reserve.enabled", True, b.start - timedelta(hours=9), "flag-console:meera.s"
    )

    truth = GroundTruth(
        family="flag-regression",
        cause=f"Flag {key} changed {old!r} → {new!r} by {actor} at {b.t0:%H:%M} with no deploy; {variant}",
        culprit={
            "service": service,
            "flag": key,
            "old": old,
            "new": new,
            "actor": actor,
            "variant": variant,
        },
        fix_action="set_flag",
        fix_target={"key": key, "value": old},
    )
    return b.finish(alert, truth)
