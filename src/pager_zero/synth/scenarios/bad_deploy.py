"""bad-deploy: a fresh deploy carries a regression; latency jumps right after rollout.

Variants (chosen by seed) change *what* the bad commit did, so the evidence trail differs:
  pool-shrink   → db_pool_wait spikes, "pool exhausted" logs
  n-plus-one    → db_qps 6x, cpu up, "slow query" logs
  sync-call     → dependency_p99 to pricing-service up, "upstream slow" logs
Correct fix: rollback_deploy(service, previous_version).
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World

VARIANTS = {
    "pool-shrink": dict(
        msg="perf: tune db pool for lower idle connections",
        files=["internal/db/pool.go", "configs/application.yml"],
        diff="--- a/configs/application.yml\n+++ b/configs/application.yml\n@@ -12,3 +12,3 @@ db:\n-  pool_max: 50\n+  pool_max: 10\n   pool_min: 5\n",
        log="db: connection pool exhausted, waiting for free conn",
    ),
    "n-plus-one": dict(
        msg="feat: show per-item discount breakdown in cart",
        files=["internal/cart/handler.go", "internal/cart/repository.go"],
        diff="--- a/internal/cart/handler.go\n+++ b/internal/cart/handler.go\n@@ -40,6 +40,9 @@ func (h *Handler) Cart(ctx, req) {\n-  items := h.repo.BulkGet(ctx, ids)\n+  var items []Item\n+  for _, id := range ids {\n+    items = append(items, h.repo.Get(ctx, id)) // TODO batch\n+  }\n",
        log="db: slow query (340ms) SELECT * FROM cart_items WHERE id = ?",
    ),
    "sync-call": dict(
        msg="feat: fetch live price before checkout confirm",
        files=["internal/checkout/confirm.go"],
        diff="--- a/internal/checkout/confirm.go\n+++ b/internal/checkout/confirm.go\n@@ -22,4 +22,7 @@ func (s *Service) Confirm(ctx, cart) {\n+  for _, it := range cart.Items {\n+    it.Price = s.pricing.Get(ctx, it.SKU) // sequential, no timeout\n+  }\n",
        log="upstream pricing-service slow: 410ms (budget 50ms)",
    ),
}


def build(seed: int) -> World:
    b = WorldBuilder("bad-deploy", seed)
    service = "checkout-api"
    variant = str(b.pick(sorted(VARIANTS)))
    v = VARIANTS[variant]
    prev = b.services[service].version

    bad = b.commit(
        service, str(v["msg"]), list(v["files"]), str(v["diff"]), b.t0 - timedelta(minutes=35)
    )
    # a harmless sibling commit in the same deploy, to make "which commit" a real question
    b.commit(
        service,
        "chore: update error messages",
        ["internal/errors.go"],
        "--- a/internal/errors.go\n+++ b/internal/errors.go\n@@ -5 +5 @@\n-'bad request'\n+'invalid request'\n",
        b.t0 - timedelta(minutes=50),
    )
    dep = b.deploy(service, b.t0 - timedelta(seconds=40), bad)

    base_p99 = (
        next(
            s for s in b.metrics if s.metric == "http_p99_ms" and s.labels["service"] == service
        ).last()
        or 200
    )
    p99 = b.series(
        "http_p99_ms",
        {"service": service, "route": "/checkout/confirm"},
        b.step(base_p99, base_p99 * 4.5),
    )
    b.recovery.append((p99, base_p99))
    # overwrite the service-level p99 series too
    b.metrics[:] = [
        s for s in b.metrics if not (s.metric == "http_p99_ms" and s.labels == {"service": service})
    ]
    svc_p99 = b.series("http_p99_ms", {"service": service}, b.step(base_p99, base_p99 * 4.2))
    b.recovery.append((svc_p99, base_p99))

    if variant == "pool-shrink":
        b.metrics[:] = [
            s
            for s in b.metrics
            if not (s.metric == "db_pool_wait_ms" and s.labels == {"service": service})
        ]
        s = b.series("db_pool_wait_ms", {"service": service}, b.step(1.5, 310))
        b.recovery.append((s, 1.5))
    elif variant == "n-plus-one":
        base_qps = (
            next(
                s for s in b.metrics if s.metric == "db_qps" and s.labels["service"] == service
            ).last()
            or 1000
        )
        b.metrics[:] = [
            s for s in b.metrics if not (s.metric == "db_qps" and s.labels == {"service": service})
        ]
        s = b.series("db_qps", {"service": service}, b.step(base_qps, base_qps * 6))
        b.recovery.append((s, base_qps))
        b.metrics[:] = [
            s
            for s in b.metrics
            if not (s.metric == "cpu_utilization" and s.labels == {"service": service})
        ]
        b.series("cpu_utilization", {"service": service}, b.step(0.4, 0.88))
    else:
        b.metrics[:] = [
            s
            for s in b.metrics
            if not (
                s.metric == "dependency_p99_ms"
                and s.labels == {"service": service, "dependency": "pricing-service"}
            )
        ]
        s = b.series(
            "dependency_p99_ms",
            {"service": service, "dependency": "pricing-service"},
            b.step(35, 420),
        )
        b.recovery.append((s, 35))

    b.burst_logs(
        service, "ERROR", str(v["log"]), b.t0, per_minute=b.rng.randint(20, 60), version=dep.version
    )
    alert = b.alert(
        service, "HighLatencyP99", f"{service} p99 > 800ms for 2m", route="/checkout/confirm"
    )
    truth = GroundTruth(
        family="bad-deploy",
        cause=f"Deploy {dep.version} of {service} (commit {bad.sha}: {bad.message}) introduced {variant.replace('-', ' ')} regression",
        culprit={"service": service, "deploy": dep.version, "commit": bad.sha, "variant": variant},
        fix_action="rollback_deploy",
        fix_target={"service": service, "to_version": prev},
    )
    return b.finish(alert, truth)
