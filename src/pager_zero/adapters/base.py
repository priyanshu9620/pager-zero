"""Adapter contracts (notes D-8).

Each Protocol is the *minimum* surface the agent needs from one kind of backend. Real
implementations (Prometheus, Kafka, Redis, GitHub, a deploy controller, a flag service) and the
synthetic world all satisfy these structurally — no inheritance required.

Read vs. mutate is explicit: read-only methods live on the `*Reader` Protocols, mutating
operations on `Actuator`. The MCP server exposes them as separate tool groups and the agent can
only reach `Actuator` after the human-approval interrupt (D-12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

# ---------- value types (plain dataclasses: cheap, JSON-able, backend-neutral) ----------

Point = tuple[datetime, float]


@dataclass(frozen=True)
class Series:
    metric: str
    labels: dict[str, str]
    points: list[Point]

    def last(self) -> float | None:
        return self.points[-1][1] if self.points else None


@dataclass(frozen=True)
class LogLine:
    ts: datetime
    service: str
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    message: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Deploy:
    service: str
    version: str
    previous_version: str
    at: datetime
    commit_sha: str
    actor: str
    status: Literal["succeeded", "rolled_back", "failed"] = "succeeded"


@dataclass(frozen=True)
class Commit:
    sha: str
    service: str
    message: str
    at: datetime
    author: str
    files: list[str]
    diff: str


@dataclass(frozen=True)
class FlagChange:
    key: str
    old: Any
    new: Any
    at: datetime
    actor: str


@dataclass(frozen=True)
class ConsumerLag:
    group: str
    topic: str
    members: int
    per_partition: dict[int, int]  # partition -> lag (messages)

    @property
    def total(self) -> int:
        return sum(self.per_partition.values())


@dataclass(frozen=True)
class CacheSnapshot:
    name: str
    max_memory_mb: int
    used_memory_mb: float
    hit_rate: float  # 0..1
    evictions_per_s: float
    keys: int


@dataclass(frozen=True)
class HotKey:
    key: str
    share: float  # fraction of partition traffic, 0..1


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: str
    target: dict[str, Any]
    detail: str


# ---------- read-only protocols ----------


@runtime_checkable
class MetricsReader(Protocol):
    def list_metrics(self, service: str) -> list[str]: ...

    def query_range(
        self,
        metric: str,
        labels: dict[str, str],
        start: datetime,
        end: datetime,
        step_s: int = 30,
    ) -> list[Series]: ...


@runtime_checkable
class LogsReader(Protocol):
    def search(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 50,
    ) -> list[LogLine]: ...


@runtime_checkable
class MessagingReader(Protocol):
    def list_groups(self, service: str) -> list[str]: ...

    def consumer_lag(self, group: str) -> ConsumerLag: ...

    def lag_history(self, group: str, start: datetime, end: datetime) -> Series: ...


@runtime_checkable
class CacheReader(Protocol):
    def list_caches(self, service: str) -> list[str]: ...

    def snapshot(self, name: str) -> CacheSnapshot: ...

    def cache_history(self, name: str, metric: str, start: datetime, end: datetime) -> Series: ...


@runtime_checkable
class DatastoreReader(Protocol):
    def list_tables(self, service: str) -> list[str]: ...

    def hot_keys(self, table: str, top: int = 5) -> list[HotKey]: ...

    def partition_latency(self, table: str, start: datetime, end: datetime) -> list[Series]: ...


@runtime_checkable
class SCMReader(Protocol):
    def recent_commits(self, service: str, since: datetime) -> list[Commit]: ...

    def diff(self, sha: str) -> str: ...


@runtime_checkable
class DeployReader(Protocol):
    def recent_deploys(self, service: str, since: datetime) -> list[Deploy]: ...

    def current_version(self, service: str) -> str: ...


@runtime_checkable
class FlagReader(Protocol):
    def get(self, key: str) -> Any: ...

    def list_keys(self, service: str) -> list[str]: ...

    def flag_history(self, key: str, since: datetime) -> list[FlagChange]: ...


@runtime_checkable
class TopologyReader(Protocol):
    """What exists and what depends on what — the agent's map."""

    def services(self) -> list[str]: ...

    def dependencies(self, service: str) -> list[str]: ...

    def dependency_health(
        self, service: str, start: datetime, end: datetime
    ) -> dict[str, Series]: ...


# ---------- mutating protocol (gated behind approval) ----------


@runtime_checkable
class Actuator(Protocol):
    def rollback_deploy(self, service: str, to_version: str) -> ActionResult: ...

    def set_flag(self, key: str, value: Any) -> ActionResult: ...

    def scale_consumers(self, group: str, members: int) -> ActionResult: ...

    def restart_consumer_group(self, group: str) -> ActionResult: ...

    def set_cache_limit(self, name: str, max_memory_mb: int) -> ActionResult: ...

    def rate_limit_key(self, table: str, key: str, rps: int) -> ActionResult: ...

    def enable_fallback(self, service: str, dependency: str) -> ActionResult: ...


# ---------- the bundle the rest of the app receives ----------


@dataclass
class Adapters:
    metrics: MetricsReader
    logs: LogsReader
    messaging: MessagingReader
    cache: CacheReader
    datastore: DatastoreReader
    scm: SCMReader
    deploys: DeployReader
    flags: FlagReader
    topology: TopologyReader
    actuator: Actuator


ACTION_NAMES: tuple[str, ...] = (
    "rollback_deploy",
    "set_flag",
    "scale_consumers",
    "restart_consumer_group",
    "set_cache_limit",
    "rate_limit_key",
    "enable_fallback",
)
