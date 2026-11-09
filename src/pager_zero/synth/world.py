"""The World: everything a synthetic incident contains, plus the hidden answer.

Design rules
------------
* One alert, one planted cause. Every other signal is either consistent with that cause or
  deliberately boring (flat), so a correct diagnosis is *derivable* from evidence, never guessed.
* Time is explicit. `t0` = the moment the cause lands (deploy, flag flip, producer burst…);
  `alert.fired_at` is a few minutes later; `now` is a few minutes after that. All series span
  [`now - window`, `now`].
* `GroundTruth` is the only thing the agent must never see. The synthetic adapter serves the
  rest; the bench reads the truth.
* `World.resolved` flips when the correct action is applied through the Actuator, so the
  `verify` step observes a real recovery (series tail returns to baseline) instead of being told.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pager_zero.adapters.base import (
    Commit,
    Deploy,
    FlagChange,
    HotKey,
    LogLine,
    Series,
)


@dataclass
class Service:
    name: str
    version: str
    replicas: int
    dependencies: list[str] = field(default_factory=list)
    consumer_groups: list[str] = field(default_factory=list)
    caches: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class ConsumerGroupState:
    group: str
    topic: str
    partitions: int
    members: int
    lag_series: Series  # total lag over time
    per_partition_now: dict[int, int]


@dataclass
class CacheState:
    name: str
    max_memory_mb: int
    keys: int
    used_mb_series: Series
    hit_rate_series: Series
    evictions_series: Series


@dataclass
class TableState:
    name: str
    hot_keys: list[HotKey]
    partition_latency: list[Series]  # one series per shard/partition


@dataclass
class Alert:
    id: str
    service: str
    name: str
    severity: str
    fired_at: datetime
    summary: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class GroundTruth:
    """What actually happened. Hidden from the agent; used by the benchmark."""

    family: str  # scenario family, e.g. "bad-deploy"
    cause: str  # one-sentence human description
    culprit: dict[str, Any]  # structured: {"deploy": "v1.57", "commit": "a81f2"} etc.
    fix_action: str  # one of adapters.base.ACTION_NAMES
    fix_target: dict[str, Any]  # exact args the correct call must contain
    acceptable_alternatives: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    # For these (action, arg) pairs a proposal is correct if it is at least / at most the target.
    _AT_LEAST = {("scale_consumers", "members"), ("set_cache_limit", "max_memory_mb")}
    _AT_MOST = {("rate_limit_key", "rps")}

    def matches(self, action: str, target: dict[str, Any]) -> bool:
        for act, want in [(self.fix_action, self.fix_target), *self.acceptable_alternatives]:
            if act == action and all(
                self._arg_ok(act, k, v, target.get(k)) for k, v in want.items()
            ):
                return True
        return False

    @classmethod
    def _arg_ok(cls, action: str, key: str, want: Any, got: Any) -> bool:
        if got is None:
            return False
        if (action, key) in cls._AT_LEAST:
            return float(got) >= float(want)
        if (action, key) in cls._AT_MOST:
            return float(got) <= float(want)
        return str(got).strip().lower() == str(want).strip().lower()


@dataclass
class World:
    scenario: str
    seed: int
    now: datetime
    t0: datetime  # when the cause landed
    window: timedelta
    alert: Alert
    services: dict[str, Service]
    deploys: list[Deploy]
    commits: list[Commit]
    flags: dict[str, Any]
    flag_history: list[FlagChange]
    metrics: list[Series]
    logs: list[LogLine]
    consumer_groups: dict[str, ConsumerGroupState]
    caches: dict[str, CacheState]
    tables: dict[str, TableState]
    dependency_health: dict[str, dict[str, Series]]  # service -> dep -> error-rate series
    ground_truth: GroundTruth
    # (series, baseline) pairs that return to normal once the correct fix is applied
    recovery_plan: list[tuple[Series, float]] = field(default_factory=list)
    # mutable runtime state
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    resolved: bool = False

    @property
    def start(self) -> datetime:
        return self.now - self.window

    def public_summary(self) -> dict[str, Any]:
        """Everything except the ground truth — what `pz world show` prints."""
        return {
            "scenario": self.scenario,
            "seed": self.seed,
            "now": self.now.isoformat(),
            "window_min": int(self.window.total_seconds() // 60),
            "alert": self.alert.__dict__,
            "services": {
                n: {
                    "version": s.version,
                    "replicas": s.replicas,
                    "deps": s.dependencies,
                    "consumer_groups": s.consumer_groups,
                    "caches": s.caches,
                    "tables": s.tables,
                    "flags": s.flags,
                }
                for n, s in self.services.items()
            },
            "deploys": [d.__dict__ for d in self.deploys],
            "commits": [{**c.__dict__, "diff": f"<{len(c.diff)} chars>"} for c in self.commits],
            "flags": self.flags,
            "flag_history": [f.__dict__ for f in self.flag_history],
            "metrics": [f"{m.metric}{m.labels} ({len(m.points)} pts)" for m in self.metrics],
            "logs": len(self.logs),
            "consumer_groups": {
                g: {
                    "topic": c.topic,
                    "members": c.members,
                    "lag_now": sum(c.per_partition_now.values()),
                }
                for g, c in self.consumer_groups.items()
            },
            "caches": {
                n: {
                    "max_mb": c.max_memory_mb,
                    "used_mb": c.used_mb_series.last(),
                    "hit_rate": c.hit_rate_series.last(),
                }
                for n, c in self.caches.items()
            },
            "tables": {n: [h.__dict__ for h in t.hot_keys] for n, t in self.tables.items()},
        }
