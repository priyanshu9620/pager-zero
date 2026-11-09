"""consumer-lag: a Kafka consumer group falls behind.

Variants:
  members-lost    consumer pods OOM-killed; members 6 → 1, pods_ready drops, lag ramps.
                  fix: scale_consumers(group, original members)  (alt: restart_consumer_group)
  producer-burst  upstream producer rate 3x after a bulk job; members unchanged, lag ramps.
                  fix: scale_consumers(group, >= 2x members)
Red herrings: a routine deploy on an *unrelated* service 20 minutes earlier.
"""

from __future__ import annotations

from datetime import timedelta

from pager_zero.synth.builder import WorldBuilder
from pager_zero.synth.world import GroundTruth, World


def build(seed: int) -> World:
    b = WorldBuilder("consumer-lag", seed)
    service, group = "order-service", "order-events-consumer"
    g = b.groups[group]
    variant = str(b.pick(["members-lost", "producer-burst"]))

    # red herring: unrelated deploy earlier
    c = b.commit(
        "pricing-service",
        "chore: bump client lib",
        ["go.mod"],
        "--- a/go.mod\n+++ b/go.mod\n@@ -3 +3 @@\n-v1.2.0\n+v1.2.1\n",
        b.t0 - timedelta(minutes=40),
    )
    b.deploy("pricing-service", b.t0 - timedelta(minutes=20), c)

    base_lag = g.lag_series.last() or 100
    b.metrics[:] = [s for s in b.metrics if s is not g.lag_series]
    if variant == "members-lost":
        lag = b.series("consumer_lag", g.lag_series.labels, b.ramp(base_lag, 4000))
        b.metrics[:] = [
            s
            for s in b.metrics
            if not (s.metric == "pods_ready" and s.labels == {"service": service})
        ]
        b.series(
            "pods_ready",
            {"service": service},
            b.step(b.services[service].replicas, 1, ramp_s=1),
            noise=0,
        )
        b.metrics[:] = [
            s for s in b.metrics if not (s.metric == "consumer_rps" and s.labels["group"] == group)
        ]
        rps = b.series(
            "consumer_rps", {"group": group, "service": service}, b.step(900, 150, ramp_s=1)
        )
        original = g.members
        g.members = 1
        for i in range(original - 1):
            b.log(
                service,
                "ERROR",
                f"pod {service}-{b.sha(i)[:5]} OOMKilled (limit 512Mi) — consumer left group {group}",
                b.t0 + timedelta(seconds=10 * i),
                pod=f"{service}-{b.sha(i)[:5]}",
            )
        b.burst_logs(
            service,
            "WARN",
            f"consumer group {group} rebalanced: 1 member for {g.partitions} partitions",
            b.t0 + timedelta(minutes=1),
            per_minute=2,
        )
        truth = GroundTruth(
            family="consumer-lag",
            cause=f"{original - 1} of {original} consumer pods in {group} were OOM-killed at {b.t0:%H:%M}; one member cannot keep up",
            culprit={
                "service": service,
                "group": group,
                "members_before": original,
                "members_now": 1,
                "variant": variant,
            },
            fix_action="scale_consumers",
            fix_target={"group": group, "members": original},
            acceptable_alternatives=[("restart_consumer_group", {"group": group})],
        )
        b.recovery += [(lag, base_lag), (rps, 900)]
    else:
        lag = b.series("consumer_lag", g.lag_series.labels, b.ramp(base_lag, 3000))
        b.series("producer_rps", {"topic": g.topic}, b.step(900, 2800))
        b.log(
            "inventory-service",
            "INFO",
            f"bulk stock sync job started: publishing 1.2M events to {g.topic}",
            b.t0 - timedelta(seconds=15),
        )
        b.burst_logs(
            service,
            "WARN",
            f"consumer {group} processing behind: fetch batch full",
            b.t0 + timedelta(minutes=2),
            per_minute=6,
        )
        truth = GroundTruth(
            family="consumer-lag",
            cause=f"Producer burst on {g.topic} (bulk sync job) at {b.t0:%H:%M}; {g.members} consumers cannot absorb 3x rate",
            culprit={
                "service": service,
                "group": group,
                "topic": g.topic,
                "producer_rps_before": 900,
                "producer_rps_now": 2800,
                "variant": variant,
            },
            fix_action="scale_consumers",
            fix_target={"group": group, "members": g.members * 2},
        )
        b.recovery.append((lag, base_lag))

    g.lag_series = lag
    g.per_partition_now = {p: int((lag.last() or 0) // g.partitions) for p in range(g.partitions)}
    alert = b.alert(service, "ConsumerLagHigh", f"{group} lag > 10k and growing", group=group)
    return b.finish(alert, truth)
