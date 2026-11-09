"""Shared machinery for scenario generators.

A `WorldBuilder` creates a boring, healthy fictional platform (services, deps, baseline metrics,
chatter logs, routine commits). Each scenario then *plants* one cause on top and declares the
ground truth. Everything is driven by `random.Random(seed)` so a (scenario, seed) pair is
reproducible.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pager_zero.adapters.base import Commit, Deploy, FlagChange, LogLine, Series
from pager_zero.synth.world import (
    Alert,
    CacheState,
    ConsumerGroupState,
    GroundTruth,
    Service,
    TableState,
    World,
)

STEP_S = 30
WINDOW = timedelta(minutes=45)

# Fictional e-commerce platform. Nothing here refers to a real company.
TOPOLOGY: dict[str, dict] = {
    "checkout-api": {
        "deps": ["order-service", "pricing-service", "payment-gateway"],
        "caches": ["checkout-session-cache"],
        "flags": ["checkout.fallback_pricing.enabled", "checkout.cart_cache.ttl_seconds"],
    },
    "order-service": {
        "deps": ["inventory-service"],
        "groups": ["order-events-consumer"],
        "tables": ["orders_by_user"],
        "flags": ["orders.batch_size", "orders.retry.enabled"],
    },
    "pricing-service": {
        "deps": ["offer-service"],
        "caches": ["price-cache"],
        "flags": ["pricing.offer_cache.ttl_seconds"],
    },
    "offer-service": {
        "deps": [],
        "caches": ["offer-cache"],
        "tables": ["offers_by_product", "offers_by_supplier"],
        "groups": ["offer-updates-consumer"],
        "flags": ["offers.time_slot_remap.enabled", "offers.cache.ttl_seconds"],
    },
    "inventory-service": {
        "deps": [],
        "tables": ["stock_by_sku"],
        "groups": ["stock-updates-consumer"],
        "flags": ["inventory.reserve.enabled"],
    },
    "payment-gateway": {"deps": [], "external": True},
}

AUTHORS = ["asha.r", "dev.k", "meera.s", "rohan.t", "yusuf.a"]
SEMVER_BASE = {
    "checkout-api": 57,
    "order-service": 112,
    "pricing-service": 41,
    "offer-service": 88,
    "inventory-service": 73,
}

Fn = Callable[[datetime], float]


class WorldBuilder:
    def __init__(self, scenario: str, seed: int) -> None:
        self.scenario = scenario
        self.seed = seed
        self.rng = random.Random(f"{scenario}:{seed}")
        # Anchor "now" to a plausible recent-ish time, varied by seed so worlds don't all look alike.
        base = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
        self.now = base + timedelta(
            hours=self.rng.randint(0, 24 * 6), minutes=self.rng.randint(0, 59)
        )
        self.window = WINDOW
        self.start = self.now - self.window
        # Cause lands 8–14 minutes before now; alert fires 2–4 minutes after the cause.
        self.t0 = self.now - timedelta(
            minutes=self.rng.randint(8, 14), seconds=self.rng.randint(0, 59)
        )
        self.alert_at = self.t0 + timedelta(minutes=self.rng.randint(2, 4))

        self.services: dict[str, Service] = {}
        self.deploys: list[Deploy] = []
        self.commits: list[Commit] = []
        self.flags: dict[str, object] = {}
        self.flag_history: list[FlagChange] = []
        self.metrics: list[Series] = []
        self.logs: list[LogLine] = []
        self.groups: dict[str, ConsumerGroupState] = {}
        self.caches: dict[str, CacheState] = {}
        self.tables: dict[str, TableState] = {}
        self.dep_health: dict[str, dict[str, Series]] = {}
        self.recovery: list[tuple[Series, float]] = []
        self._build_baseline()

    # ------------------------------------------------------------------ primitives

    def sha(self, *parts: object) -> str:
        return hashlib.sha1(f"{self.seed}:{'|'.join(map(str, parts))}".encode()).hexdigest()[:7]

    def pick(self, xs: list) -> object:
        return self.rng.choice(xs)

    def series(self, metric: str, labels: dict[str, str], fn: Fn, noise: float = 0.03) -> Series:
        pts = []
        t = self.start
        while t <= self.now:
            v = fn(t)
            v = v * (1 + self.rng.uniform(-noise, noise)) if v else 0.0
            pts.append((t, round(v, 3)))
            t += timedelta(seconds=STEP_S)
        s = Series(metric, labels, pts)
        self.metrics.append(s)
        return s

    @staticmethod
    def flat(v: float) -> Fn:
        return lambda _t: v

    def step(self, before: float, after: float, at: datetime | None = None, ramp_s: int = 60) -> Fn:
        at = at or self.t0

        def fn(t: datetime) -> float:
            if t < at:
                return before
            frac = min(1.0, (t - at).total_seconds() / ramp_s)
            return before + (after - before) * frac

        return fn

    def ramp(self, before: float, per_minute: float, at: datetime | None = None) -> Fn:
        at = at or self.t0
        return lambda t: before if t < at else before + per_minute * (t - at).total_seconds() / 60

    def log(self, service: str, level: str, message: str, at: datetime, **fields: object) -> None:
        self.logs.append(LogLine(at, service, level, message, dict(fields)))  # type: ignore[arg-type]

    def burst_logs(
        self,
        service: str,
        level: str,
        message: str,
        start: datetime,
        per_minute: int,
        **fields: object,
    ) -> None:
        t = start
        while t <= self.now:
            for _ in range(per_minute):
                at = t + timedelta(seconds=self.rng.randint(0, 59))
                if at <= self.now:  # never emit a log line from the future
                    self.log(service, level, message, at, **fields)
            t += timedelta(minutes=1)

    def commit(
        self,
        service: str,
        message: str,
        files: list[str],
        diff: str,
        at: datetime,
        author: str | None = None,
    ) -> Commit:
        c = Commit(
            self.sha(service, message, at),
            service,
            message,
            at,
            author or str(self.pick(AUTHORS)),
            files,
            diff,
        )
        self.commits.append(c)
        return c

    def deploy(self, service: str, at: datetime, commit: Commit, bump: int = 1) -> Deploy:
        svc = self.services[service]
        major, minor = svc.version[1:].split(".")
        new = f"v{major}.{int(minor) + bump}"
        d = Deploy(service, new, svc.version, at, commit.sha, str(self.pick(AUTHORS)))
        self.deploys.append(d)
        svc.version = new
        self.log(
            service, "INFO", f"deploy {new} rolled out ({commit.sha})", at + timedelta(seconds=20)
        )
        return d

    def flip_flag(self, key: str, new: object, at: datetime, actor: str = "flag-console") -> None:
        old = self.flags[key]
        self.flags[key] = new
        self.flag_history.append(FlagChange(key, old, new, at, actor))

    def alert(
        self, service: str, name: str, summary: str, severity: str = "high", **labels: str
    ) -> Alert:
        return Alert(
            f"alt_{self.sha('alert')}",
            service,
            name,
            severity,
            self.alert_at,
            summary,
            dict(labels),
        )

    def finish(self, alert: Alert, truth: GroundTruth) -> World:
        self.logs.sort(key=lambda line: line.ts)
        self.commits.sort(key=lambda c: c.at)
        self.deploys.sort(key=lambda d: d.at)
        return World(
            scenario=self.scenario,
            seed=self.seed,
            now=self.now,
            t0=self.t0,
            window=self.window,
            alert=alert,
            services=self.services,
            deploys=self.deploys,
            commits=self.commits,
            flags=self.flags,
            flag_history=self.flag_history,
            metrics=self.metrics,
            logs=self.logs,
            consumer_groups=self.groups,
            caches=self.caches,
            tables=self.tables,
            dependency_health=self.dep_health,
            ground_truth=truth,
            recovery_plan=self.recovery,
        )

    # ------------------------------------------------------------------ baseline platform

    def _build_baseline(self) -> None:
        for name, spec in TOPOLOGY.items():
            if spec.get("external"):
                self.services[name] = Service(name, "external", 0, [])
                continue
            minor = SEMVER_BASE[name] + self.rng.randint(0, 3)
            self.services[name] = Service(
                name,
                f"v1.{minor}",
                self.rng.choice([4, 6, 8, 12]),
                list(spec.get("deps", [])),
                list(spec.get("groups", [])),
                list(spec.get("caches", [])),
                list(spec.get("tables", [])),
                list(spec.get("flags", [])),
            )
        # flags with sane defaults
        defaults = {
            "checkout.fallback_pricing.enabled": True,
            "checkout.cart_cache.ttl_seconds": 300,
            "orders.batch_size": 100,
            "orders.retry.enabled": True,
            "pricing.offer_cache.ttl_seconds": 120,
            "offers.time_slot_remap.enabled": True,
            "offers.cache.ttl_seconds": 600,
            "inventory.reserve.enabled": True,
        }
        self.flags.update(defaults)

        for name, svc in self.services.items():
            if svc.version == "external":
                continue
            base_p99 = self.rng.choice([120, 180, 210, 260])
            base_rps = self.rng.choice([800, 1500, 2400, 4000])
            self.series("http_p99_ms", {"service": name}, self.flat(base_p99))
            self.series("http_rps", {"service": name}, self.flat(base_rps))
            self.series("http_error_rate", {"service": name}, self.flat(0.002), noise=0.4)
            self.series("cpu_utilization", {"service": name}, self.flat(self.rng.uniform(0.3, 0.5)))
            self.series("db_pool_wait_ms", {"service": name}, self.flat(1.5), noise=0.3)
            self.series("db_qps", {"service": name}, self.flat(base_rps * 0.6))
            self.series("pods_ready", {"service": name}, self.flat(svc.replicas), noise=0)
            for dep in svc.dependencies:
                s = self.series(
                    "dependency_error_rate",
                    {"service": name, "dependency": dep},
                    self.flat(0.001),
                    noise=0.5,
                )
                self.dep_health.setdefault(name, {})[dep] = s
                self.series(
                    "dependency_p99_ms",
                    {"service": name, "dependency": dep},
                    self.flat(self.rng.choice([20, 35, 60])),
                )
            for g in svc.consumer_groups:
                self._baseline_group(g, name)
            for c in svc.caches:
                self._baseline_cache(c, name)
            for tname in svc.tables:
                self._baseline_table(tname, name)
            # chatter
            n = self.rng.randint(8, 16)
            for _ in range(n):
                at = self.start + timedelta(
                    seconds=self.rng.randint(0, int(self.window.total_seconds()))
                )
                self.log(
                    name,
                    "INFO",
                    self.rng.choice(
                        [
                            "health check ok",
                            "gc pause 12ms",
                            "refreshed config from flag service",
                            "request completed",
                        ]
                    ),
                    at,
                )
            # one routine old commit per service (noise for the SCM tool)
            at = self.start - timedelta(hours=self.rng.randint(5, 40))
            self.commit(
                name,
                self.rng.choice(
                    [
                        "chore: bump deps",
                        "docs: update README",
                        "test: add table-driven cases",
                        "refactor: rename handler",
                    ]
                ),
                ["README.md"],
                "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
                at,
            )

    def _baseline_group(self, group: str, service: str) -> None:
        parts = self.rng.choice([6, 12, 24])
        members = self.rng.choice([3, 6])
        lag = self.series(
            "consumer_lag",
            {"group": group, "service": service},
            self.flat(self.rng.choice([40, 120, 300])),
            noise=0.3,
        )
        self.series(
            "consumer_rps",
            {"group": group, "service": service},
            self.flat(self.rng.choice([300, 900, 1500])),
        )
        per = {p: int(lag.last() or 0) // parts for p in range(parts)}
        self.groups[group] = ConsumerGroupState(
            group, group.replace("-consumer", ""), parts, members, lag, per
        )

    def _baseline_cache(self, name: str, service: str) -> None:
        max_mb = self.rng.choice([2048, 4096, 8192])
        used = self.series(
            "cache_used_mb",
            {"cache": name, "service": service},
            self.flat(max_mb * self.rng.uniform(0.45, 0.6)),
            noise=0.01,
        )
        hit = self.series(
            "cache_hit_rate",
            {"cache": name, "service": service},
            self.flat(self.rng.uniform(0.94, 0.985)),
            noise=0.005,
        )
        ev = self.series(
            "cache_evictions_per_s", {"cache": name, "service": service}, self.flat(0.5), noise=0.5
        )
        self.caches[name] = CacheState(
            name, max_mb, self.rng.randint(2_000_000, 9_000_000), used, hit, ev
        )

    def _baseline_table(self, name: str, service: str) -> None:
        shards = 4
        lat = [
            self.series(
                "partition_p99_ms",
                {"table": name, "shard": str(i)},
                self.flat(self.rng.choice([4, 6, 8])),
            )
            for i in range(shards)
        ]
        keys = [f"{name.split('_by_')[-1]}:{self.rng.randint(1000, 9999)}" for _ in range(5)]
        shares = sorted([self.rng.uniform(0.01, 0.04) for _ in keys], reverse=True)
        self.tables[name] = TableState(
            name, [HotKeyLite(k, round(s, 3)) for k, s in zip(keys, shares, strict=True)], lat
        )


# tiny local alias so builder doesn't import HotKey name into scenario modules by accident
from pager_zero.adapters.base import HotKey as HotKeyLite  # noqa: E402

__all__ = ["WorldBuilder", "STEP_S", "WINDOW", "TOPOLOGY", "math"]
