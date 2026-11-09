"""Synthetic backend: serves a generated `World` through every adapter Protocol.

One class implements all readers + the actuator over the same World object. Mutating calls
are recorded; if the call matches the hidden ground truth the world "heals": `now` advances
and the affected series return to baseline, so the agent's verify step sees a real recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pager_zero.adapters.base import (
    ActionResult,
    Adapters,
    CacheSnapshot,
    Commit,
    ConsumerLag,
    Deploy,
    FlagChange,
    HotKey,
    LogLine,
    Series,
)
from pager_zero.synth import generate
from pager_zero.synth.builder import STEP_S
from pager_zero.synth.world import World

RECOVERY_MINUTES = 3


class SyntheticAdapters:
    def __init__(self, world: World) -> None:
        self.world = world

    @classmethod
    def from_settings(
        cls, scenario: str = "bad-deploy", seed: int = 1, world: World | None = None
    ) -> Adapters:
        impl = cls(world or generate(scenario, seed))
        return Adapters(*([impl] * 10))  # same object satisfies every Protocol

    # ---------------- helpers
    def _clip(self, s: Series, start: datetime, end: datetime) -> Series:
        return Series(s.metric, s.labels, [(t, v) for t, v in s.points if start <= t <= end])

    def _match(self, s: Series, metric: str, labels: dict[str, str]) -> bool:
        return s.metric == metric and all(s.labels.get(k) == v for k, v in labels.items())

    # ---------------- MetricsReader
    def list_metrics(self, service: str) -> list[str]:
        return sorted({s.metric for s in self.world.metrics if s.labels.get("service") == service})

    def query_range(
        self, metric: str, labels: dict[str, str], start: datetime, end: datetime, step_s: int = 30
    ) -> list[Series]:
        return [
            self._clip(s, start, end) for s in self.world.metrics if self._match(s, metric, labels)
        ]

    # ---------------- LogsReader
    def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 50
    ) -> list[LogLine]:
        q = query.lower().strip()
        out = [
            line
            for line in self.world.logs
            if line.service == service
            and start <= line.ts <= end
            and (not q or q in line.message.lower() or q == line.level.lower())
        ]
        return out[-limit:]

    # ---------------- MessagingReader
    def list_groups(self, service: str) -> list[str]:
        return list(self.world.services[service].consumer_groups)

    def consumer_lag(self, group: str) -> ConsumerLag:
        g = self.world.consumer_groups[group]
        return ConsumerLag(group, g.topic, g.members, dict(g.per_partition_now))

    def lag_history(self, group: str, start: datetime, end: datetime) -> Series:
        return self._clip(self.world.consumer_groups[group].lag_series, start, end)

    # ---------------- CacheReader
    def list_caches(self, service: str) -> list[str]:
        return list(self.world.services[service].caches)

    def snapshot(self, name: str) -> CacheSnapshot:
        c = self.world.caches[name]
        return CacheSnapshot(
            name,
            c.max_memory_mb,
            c.used_mb_series.last() or 0.0,
            c.hit_rate_series.last() or 0.0,
            c.evictions_series.last() or 0.0,
            c.keys,
        )

    def cache_history(self, name: str, metric: str, start: datetime, end: datetime) -> Series:
        c = self.world.caches[name]
        s = {
            "used_mb": c.used_mb_series,
            "hit_rate": c.hit_rate_series,
            "evictions": c.evictions_series,
        }[metric]
        return self._clip(s, start, end)

    # ---------------- DatastoreReader
    def list_tables(self, service: str) -> list[str]:
        return list(self.world.services[service].tables)

    def hot_keys(self, table: str, top: int = 5) -> list[HotKey]:
        return sorted(self.world.tables[table].hot_keys, key=lambda h: -h.share)[:top]

    def partition_latency(self, table: str, start: datetime, end: datetime) -> list[Series]:
        return [self._clip(s, start, end) for s in self.world.tables[table].partition_latency]

    # ---------------- SCMReader
    def recent_commits(self, service: str, since: datetime) -> list[Commit]:
        return [c for c in self.world.commits if c.service == service and c.at >= since]

    def diff(self, sha: str) -> str:
        for c in self.world.commits:
            if c.sha == sha:
                return c.diff
        raise KeyError(f"unknown commit {sha}")

    # ---------------- DeployReader
    def recent_deploys(self, service: str, since: datetime) -> list[Deploy]:
        return [d for d in self.world.deploys if d.service == service and d.at >= since]

    def current_version(self, service: str) -> str:
        return self.world.services[service].version

    # ---------------- FlagReader
    def get(self, key: str) -> Any:
        return self.world.flags[key]

    def list_keys(self, service: str) -> list[str]:
        return list(self.world.services[service].flags)

    def flag_history(self, key: str, since: datetime) -> list[FlagChange]:
        return [f for f in self.world.flag_history if f.key == key and f.at >= since]

    # ---------------- TopologyReader
    def services(self) -> list[str]:
        return list(self.world.services)

    def dependencies(self, service: str) -> list[str]:
        return list(self.world.services[service].dependencies)

    def dependency_health(self, service: str, start: datetime, end: datetime) -> dict[str, Series]:
        return {
            d: self._clip(s, start, end)
            for d, s in self.world.dependency_health.get(service, {}).items()
        }

    # ---------------- Actuator (gated upstream by the approval interrupt)
    def _act(self, action: str, target: dict[str, Any], detail: str) -> ActionResult:
        self.world.actions_taken.append(
            {"action": action, "target": target, "at": self.world.now.isoformat()}
        )
        if self.world.ground_truth.matches(action, target):
            self._heal()
            detail += " — metrics recovering"
        return ActionResult(True, action, target, detail)

    def _heal(self) -> None:
        w = self.world
        if w.resolved:
            return
        w.resolved = True
        start = w.now
        w.now = w.now + timedelta(minutes=RECOVERY_MINUTES)
        for s, baseline in w.recovery_plan:
            t = start + timedelta(seconds=STEP_S)
            n = 0
            while t <= w.now:
                frac = min(1.0, n / 4)  # recover within ~2 minutes
                last = s.points[-1][1] if s.points else baseline
                s.points.append((t, round(last + (baseline - last) * frac, 3)))
                t += timedelta(seconds=STEP_S)
                n += 1
        for g in w.consumer_groups.values():
            g.per_partition_now = {
                p: int((g.lag_series.last() or 0) // g.partitions) for p in range(g.partitions)
            }

    def rollback_deploy(self, service: str, to_version: str) -> ActionResult:
        svc = self.world.services[service]
        prev = svc.version
        svc.version = to_version
        self.world.deploys.append(
            Deploy(service, to_version, prev, self.world.now, "rollback", "pager-zero", "succeeded")
        )
        return self._act(
            "rollback_deploy",
            {"service": service, "to_version": to_version},
            f"{service}: {prev} → {to_version}",
        )

    def set_flag(self, key: str, value: Any) -> ActionResult:
        old = self.world.flags.get(key)
        self.world.flags[key] = value
        self.world.flag_history.append(FlagChange(key, old, value, self.world.now, "pager-zero"))
        return self._act("set_flag", {"key": key, "value": value}, f"{key}: {old!r} → {value!r}")

    def scale_consumers(self, group: str, members: int) -> ActionResult:
        g = self.world.consumer_groups[group]
        before, g.members = g.members, members
        return self._act(
            "scale_consumers",
            {"group": group, "members": members},
            f"{group}: {before} → {members} members",
        )

    def restart_consumer_group(self, group: str) -> ActionResult:
        return self._act(
            "restart_consumer_group", {"group": group}, f"{group}: rolling restart issued"
        )

    def set_cache_limit(self, name: str, max_memory_mb: int) -> ActionResult:
        c = self.world.caches[name]
        before, c.max_memory_mb = c.max_memory_mb, max_memory_mb
        return self._act(
            "set_cache_limit",
            {"name": name, "max_memory_mb": max_memory_mb},
            f"{name}: maxmemory {before} → {max_memory_mb} MB",
        )

    def rate_limit_key(self, table: str, key: str, rps: int) -> ActionResult:
        return self._act(
            "rate_limit_key",
            {"table": table, "key": key, "rps": rps},
            f"{table}: key {key} limited to {rps} rps",
        )

    def enable_fallback(self, service: str, dependency: str) -> ActionResult:
        return self._act(
            "enable_fallback",
            {"service": service, "dependency": dependency},
            f"{service}: fallback enabled for {dependency}",
        )
