"""downstream-outage: a dependency starts failing; the caller's retries amplify the damage.

The dependency is either the external payment-gateway or an internal service whose own
metrics show the failure. Nothing changed in the alerting service (no deploy, no flag).
Correct fix: enable_fallback(service, dependency) — degrade gracefully, stop retry amplification.
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World


def build(seed: int) -> World:
    b = WorldBuilder("downstream-outage", seed)
    service = "checkout-api"
    dependency = str(b.pick(["payment-gateway", "pricing-service", "order-service"]))

    dep = b.dep_health[service][dependency]
    b.metrics.remove(dep)
    err = b.series("dependency_error_rate", dep.labels, b.step(0.001, 0.62, ramp_s=90))
    b.dep_health[service][dependency] = err
    b.recovery.append((err, 0.001))  # fallback masks the dependency from the caller's point of view
    lat = next(
        s
        for s in b.metrics
        if s.metric == "dependency_p99_ms"
        and s.labels == {"service": service, "dependency": dependency}
    )
    b.metrics.remove(lat)
    b.series("dependency_p99_ms", lat.labels, b.step(lat.last() or 40, 5000, ramp_s=90))

    p99 = next(
        s for s in b.metrics if s.metric == "http_p99_ms" and s.labels == {"service": service}
    )
    b.metrics.remove(p99)
    s = b.series("http_p99_ms", p99.labels, b.step(p99.last() or 200, 6200, ramp_s=120))
    b.recovery.append((s, p99.last() or 200))
    e = next(
        s for s in b.metrics if s.metric == "http_error_rate" and s.labels == {"service": service}
    )
    b.metrics.remove(e)
    s = b.series("http_error_rate", e.labels, b.step(0.002, 0.31, ramp_s=120))
    b.recovery.append((s, 0.002))
    b.series(
        "retries_per_s", {"service": service, "dependency": dependency}, b.step(2, 1800, ramp_s=90)
    )

    if dependency != "payment-gateway":
        d_err = next(
            s
            for s in b.metrics
            if s.metric == "http_error_rate" and s.labels == {"service": dependency}
        )
        b.metrics.remove(d_err)
        b.series("http_error_rate", d_err.labels, b.step(0.002, 0.7, ramp_s=60))
        b.burst_logs(
            dependency,
            "ERROR",
            "panic: runtime error: invalid memory address (recovered) in handler",
            b.t0,
            per_minute=40,
        )
    else:
        b.log(
            service,
            "WARN",
            "payment-gateway status page: 'Degraded performance — API errors'",
            b.t0 + timedelta(minutes=1),
        )

    b.burst_logs(
        service,
        "ERROR",
        f"call to {dependency} failed: 503 after 3 retries (timeout 2s)",
        b.t0 + timedelta(seconds=30),
        per_minute=60,
        dependency=dependency,
    )

    alert = b.alert(service, "HighErrorRate", f"{service} 5xx rate > 2% for 2m")
    truth = GroundTruth(
        family="downstream-outage",
        cause=f"{dependency} began returning errors at {b.t0:%H:%M}; {service} retries (3x, 2s timeout) amplified load and latency",
        culprit={"service": service, "dependency": dependency},
        fix_action="enable_fallback",
        fix_target={"service": service, "dependency": dependency},
    )
    return b.finish(alert, truth)
