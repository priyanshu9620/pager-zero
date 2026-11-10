"""Live backend (notes Phase 7, D-35): thin real implementations of the adapter Protocols.

  MetricsReader     Prometheus HTTP API            (/api/v1/query_range)
  LogsReader        Loki HTTP API                  (/loki/api/v1/query_range) — optional
  MessagingReader   Kafka admin offsets            (kafka-python) + Prometheus for lag history
  CacheReader       Redis INFO                     (redis-py)      + Prometheus for history
  SCMReader         GitHub REST                    (httpx)
  DeployReader      file-backed deploy log         (JSON written by your CI)
  FlagReader        file-backed flag store         (JSON written by your flag service)
  DatastoreReader   Prometheus-only                (hot keys need a datastore-specific exporter)
  TopologyReader    static topology file           (JSON: services → deps/groups/caches/tables/flags)
  Actuator          DRY-RUN by default             (logs the intended action; never mutates)

Everything is constructed from `Settings`; each class is small enough to be replaced in an hour.
HTTP clients are injectable so the tests use `httpx.MockTransport` instead of a network.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

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

log = logging.getLogger(__name__)


def _ts(x: float | str) -> datetime:
    return datetime.fromtimestamp(float(x), tz=UTC)


# ------------------------------------------------------------------ Prometheus


class PrometheusMetrics:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.http = client or httpx.Client(timeout=10)

    @staticmethod
    def selector(metric: str, labels: dict[str, str]) -> str:
        if not labels:
            return metric
        inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{metric}{{{inner}}}"

    def query_range(
        self, metric: str, labels: dict[str, str], start: datetime, end: datetime, step_s: int = 30
    ) -> list[Series]:
        r = self.http.get(
            f"{self.base}/api/v1/query_range",
            params={
                "query": self.selector(metric, labels),
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": f"{step_s}s",
            },
        )
        r.raise_for_status()
        out = []
        for item in r.json()["data"]["result"]:
            lbls = {k: v for k, v in item["metric"].items() if k != "__name__"}
            pts = [(_ts(t), float(v)) for t, v in item["values"]]
            out.append(Series(metric, lbls, pts))
        return out

    def list_metrics(self, service: str) -> list[str]:
        r = self.http.get(
            f"{self.base}/api/v1/label/__name__/values",
            params={"match[]": f'{{service="{service}"}}'},
        )
        r.raise_for_status()
        return sorted(r.json().get("data", []))

    # used by other live readers for histories
    def one(self, metric: str, labels: dict[str, str], start: datetime, end: datetime) -> Series:
        s = self.query_range(metric, labels, start, end)
        return s[0] if s else Series(metric, labels, [])


class LokiLogs:
    def __init__(self, base_url: str | None, client: httpx.Client | None = None) -> None:
        self.base = base_url.rstrip("/") if base_url else None
        self.http = client or httpx.Client(timeout=10)

    def search(
        self, service: str, query: str, start: datetime, end: datetime, limit: int = 50
    ) -> list[LogLine]:
        if not self.base:
            return []
        q = f'{{service="{service}"}}' + (f' |= "{query}"' if query else "")
        r = self.http.get(
            f"{self.base}/loki/api/v1/query_range",
            params={
                "query": q,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
            },
        )
        r.raise_for_status()
        lines: list[LogLine] = []
        for stream in r.json()["data"]["result"]:
            level = str(stream["stream"].get("level", "INFO")).upper()
            for ts_ns, msg in stream["values"]:
                lines.append(
                    LogLine(
                        datetime.fromtimestamp(int(ts_ns) / 1e9, tz=UTC),
                        service,
                        level if level in ("DEBUG", "INFO", "WARN", "ERROR") else "INFO",
                        msg,
                    )
                )  # type: ignore[arg-type]
        lines.sort(key=lambda line: line.ts)
        return lines[-limit:]


# ------------------------------------------------------------------ Kafka


class KafkaMessaging:
    """Consumer lag from committed offsets vs end offsets. `admin`/`consumer` are injectable."""

    def __init__(
        self,
        bootstrap: str,
        topology: StaticTopology,
        prom: PrometheusMetrics,
        admin: Any = None,
        consumer_factory: Any = None,
    ) -> None:
        self.bootstrap = bootstrap
        self.topology = topology
        self.prom = prom
        self._admin = admin
        self._consumer_factory = consumer_factory

    def _clients(self):
        if self._admin is None:
            from kafka import KafkaAdminClient, KafkaConsumer

            self._admin = KafkaAdminClient(bootstrap_servers=self.bootstrap)
            self._consumer_factory = lambda: KafkaConsumer(bootstrap_servers=self.bootstrap)
        return self._admin, self._consumer_factory

    def list_groups(self, service: str) -> list[str]:
        return self.topology.svc(service).get("consumer_groups", [])

    def consumer_lag(self, group: str) -> ConsumerLag:
        admin, make_consumer = self._clients()
        # kafka-python 3.x: {group: {TopicPartition: OffsetAndMetadata}}
        committed = admin.list_group_offsets({group: None}).get(group, {})
        if not committed:
            return ConsumerLag(group, "", 0, {})
        consumer = make_consumer()
        ends = consumer.end_offsets(list(committed))
        per = {tp.partition: max(0, ends[tp] - om.offset) for tp, om in committed.items()}
        topic = next(iter(committed)).topic
        members = 0
        try:
            desc = admin.describe_groups([group])  # {group: {..., "members": [...]}}
            members = len((desc.get(group) or {}).get("members", []))
        except Exception as e:  # noqa: BLE001
            log.debug("describe_groups failed: %s", e)
        return ConsumerLag(group, topic, members, per)

    def lag_history(self, group: str, start: datetime, end: datetime) -> Series:
        return self.prom.one("kafka_consumergroup_lag_sum", {"consumergroup": group}, start, end)


# ------------------------------------------------------------------ Redis


class RedisCache:
    def __init__(
        self, url: str, topology: StaticTopology, prom: PrometheusMetrics, client: Any = None
    ) -> None:
        self.url = url
        self.topology = topology
        self.prom = prom
        self._client = client

    def _r(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self.url, socket_timeout=3)
        return self._client

    def list_caches(self, service: str) -> list[str]:
        return self.topology.svc(service).get("caches", [])

    def snapshot(self, name: str) -> CacheSnapshot:
        info = self._r().info()
        hits, misses = int(info.get("keyspace_hits", 0)), int(info.get("keyspace_misses", 0))
        keys = sum(
            int(v["keys"]) for k, v in info.items() if k.startswith("db") and isinstance(v, dict)
        )
        return CacheSnapshot(
            name,
            int(info.get("maxmemory", 0)) // (1024 * 1024),
            int(info.get("used_memory", 0)) / (1024 * 1024),
            hits / (hits + misses) if hits + misses else 0.0,
            float(info.get("evicted_keys_per_sec", 0) or 0),
            keys,
        )

    def cache_history(self, name: str, metric: str, start: datetime, end: datetime) -> Series:
        prom_metric = {
            "hit_rate": "redis_keyspace_hit_ratio",
            "used_mb": "redis_memory_used_bytes",
            "evictions": "redis_evicted_keys_total",
        }[metric]
        return self.prom.one(prom_metric, {"instance": name}, start, end)


# ------------------------------------------------------------------ GitHub


class GitHubSCM:
    def __init__(
        self, token: str | None, repos: dict[str, str], client: httpx.Client | None = None
    ) -> None:
        """`repos` maps service name → "owner/repo"."""
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.repos = repos
        self.http = client or httpx.Client(
            base_url="https://api.github.com", headers=headers, timeout=10
        )

    def recent_commits(self, service: str, since: datetime) -> list[Commit]:
        repo = self.repos.get(service)
        if not repo:
            return []
        r = self.http.get(
            f"/repos/{repo}/commits", params={"since": since.isoformat(), "per_page": 50}
        )
        r.raise_for_status()
        out = []
        for c in r.json():
            out.append(
                Commit(
                    c["sha"][:7],
                    service,
                    c["commit"]["message"].splitlines()[0],
                    datetime.fromisoformat(c["commit"]["author"]["date"].replace("Z", "+00:00")),
                    c["commit"]["author"]["name"],
                    [],  # files need a second call; fetched lazily via diff()
                    "",
                )
            )
        self._sha_repo = {c.sha: repo for c in out} | getattr(self, "_sha_repo", {})
        return out

    def diff(self, sha: str) -> str:
        repo = getattr(self, "_sha_repo", {}).get(sha) or next(iter(self.repos.values()), None)
        if not repo:
            raise KeyError(sha)
        r = self.http.get(
            f"/repos/{repo}/commits/{sha}", headers={"Accept": "application/vnd.github.diff"}
        )
        r.raise_for_status()
        return r.text


# ------------------------------------------------------------------ file-backed deploys / flags, static topology


class FileDeployLog:
    """JSON list of {service, version, previous_version, at, commit_sha, actor, status}. Your CI appends to it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _all(self) -> list[Deploy]:
        if not self.path.exists():
            return []
        return [
            Deploy(
                d["service"],
                d["version"],
                d.get("previous_version", ""),
                datetime.fromisoformat(d["at"]),
                d.get("commit_sha", ""),
                d.get("actor", ""),
                d.get("status", "succeeded"),
            )
            for d in json.loads(self.path.read_text())
        ]

    def recent_deploys(self, service: str, since: datetime) -> list[Deploy]:
        return [d for d in self._all() if d.service == service and d.at >= since]

    def current_version(self, service: str) -> str:
        ds = [d for d in self._all() if d.service == service]
        return max(ds, key=lambda d: d.at).version if ds else "unknown"


class FileFlagStore:
    """JSON {"values": {key: value}, "history": [{key, old, new, at, actor}]}."""

    def __init__(self, path: Path, topology: StaticTopology) -> None:
        self.path = path
        self.topology = topology

    def _doc(self) -> dict[str, Any]:
        return (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"values": {}, "history": []}
        )

    def get(self, key: str) -> Any:
        return self._doc()["values"].get(key)

    def list_keys(self, service: str) -> list[str]:
        return self.topology.svc(service).get("flags", [])

    def flag_history(self, key: str, since: datetime) -> list[FlagChange]:
        return [
            FlagChange(
                h["key"],
                h.get("old"),
                h.get("new"),
                datetime.fromisoformat(h["at"]),
                h.get("actor", ""),
            )
            for h in self._doc()["history"]
            if h["key"] == key and datetime.fromisoformat(h["at"]) >= since
        ]

    def set(self, key: str, value: Any, actor: str = "pager-zero") -> Any:
        doc = self._doc()
        old = doc["values"].get(key)
        doc["values"][key] = value
        doc["history"].append(
            {
                "key": key,
                "old": old,
                "new": value,
                "at": datetime.now(UTC).isoformat(),
                "actor": actor,
            }
        )
        self.path.write_text(json.dumps(doc, indent=2, default=str))
        return old


class StaticTopology:
    """JSON {"services": {name: {dependencies, consumer_groups, caches, tables, flags, repo}}}."""

    def __init__(self, path: Path, prom: PrometheusMetrics) -> None:
        self.doc = json.loads(path.read_text()) if path.exists() else {"services": {}}
        self.prom = prom

    def svc(self, service: str) -> dict[str, Any]:
        return self.doc["services"].get(service, {})

    def services(self) -> list[str]:
        return list(self.doc["services"])

    def dependencies(self, service: str) -> list[str]:
        return self.svc(service).get("dependencies", [])

    def dependency_health(self, service: str, start: datetime, end: datetime) -> dict[str, Series]:
        return {
            d: self.prom.one(
                "dependency_error_rate", {"service": service, "dependency": d}, start, end
            )
            for d in self.dependencies(service)
        }

    def repos(self) -> dict[str, str]:
        return {n: s["repo"] for n, s in self.doc["services"].items() if s.get("repo")}


class PrometheusDatastore:
    """Partition latency from Prometheus; hot keys need a datastore-specific exporter (not provided)."""

    def __init__(self, topology: StaticTopology, prom: PrometheusMetrics) -> None:
        self.topology = topology
        self.prom = prom

    def list_tables(self, service: str) -> list[str]:
        return self.topology.svc(service).get("tables", [])

    def hot_keys(self, table: str, top: int = 5) -> list[HotKey]:
        return []

    def partition_latency(self, table: str, start: datetime, end: datetime) -> list[Series]:
        return self.prom.query_range("partition_p99_ms", {"table": table}, start, end)


# ------------------------------------------------------------------ actuator


class DryRunActuator:
    """Records what WOULD be done. Wire a real one deliberately; never by default (D-35)."""

    def __init__(
        self,
        flags: FileFlagStore | None = None,
        deploy_log: FileDeployLog | None = None,
        dry_run: bool = True,
    ) -> None:
        self.flags, self.deploys, self.dry_run = flags, deploy_log, dry_run
        self.log: list[dict[str, Any]] = []

    def _rec(self, action: str, target: dict[str, Any], detail: str) -> ActionResult:
        self.log.append({"action": action, "target": target, "at": datetime.now(UTC).isoformat()})
        prefix = "DRY-RUN (not applied): " if self.dry_run else ""
        log.warning("%s%s %s", prefix, action, target)
        return ActionResult(True, action, target, prefix + detail)

    def rollback_deploy(self, service: str, to_version: str) -> ActionResult:
        return self._rec(
            "rollback_deploy",
            {"service": service, "to_version": to_version},
            f"{service} → {to_version}",
        )

    def set_flag(self, key: str, value: Any) -> ActionResult:
        if not self.dry_run and self.flags:
            old = self.flags.set(key, value)
            return self._rec(
                "set_flag", {"key": key, "value": value}, f"{key}: {old!r} → {value!r}"
            )
        return self._rec("set_flag", {"key": key, "value": value}, f"{key} → {value!r}")

    def scale_consumers(self, group: str, members: int) -> ActionResult:
        return self._rec(
            "scale_consumers", {"group": group, "members": members}, f"{group} → {members} members"
        )

    def restart_consumer_group(self, group: str) -> ActionResult:
        return self._rec("restart_consumer_group", {"group": group}, f"{group} restart")

    def set_cache_limit(self, name: str, max_memory_mb: int) -> ActionResult:
        return self._rec(
            "set_cache_limit",
            {"name": name, "max_memory_mb": max_memory_mb},
            f"{name} maxmemory → {max_memory_mb} MB",
        )

    def rate_limit_key(self, table: str, key: str, rps: int) -> ActionResult:
        return self._rec(
            "rate_limit_key", {"table": table, "key": key, "rps": rps}, f"{table}:{key} → {rps} rps"
        )

    def enable_fallback(self, service: str, dependency: str) -> ActionResult:
        return self._rec(
            "enable_fallback",
            {"service": service, "dependency": dependency},
            f"{service} fallback for {dependency}",
        )


# ------------------------------------------------------------------ bundle


class LiveAdapters:
    @staticmethod
    def from_settings(**_: Any) -> Adapters:
        from pager_zero.config import settings

        prom = PrometheusMetrics(settings.prometheus_url)
        topo = StaticTopology(settings.live_topology_file, prom)
        flags = FileFlagStore(settings.live_flag_file, topo)
        deploys = FileDeployLog(settings.live_deploy_log)
        actuator: Any = DryRunActuator(flags, deploys, dry_run=settings.actuator_dry_run)
        if settings.actuator == "demo":
            from pager_zero.demo import DemoStack
            from pager_zero.demo.actuator import DemoActuator

            actuator = DemoActuator(DemoStack(settings.demo_dir, settings.redis_url))
        return Adapters(
            metrics=prom,
            logs=LokiLogs(settings.loki_url),
            messaging=KafkaMessaging(settings.kafka_bootstrap, topo, prom),
            cache=RedisCache(settings.redis_url, topo, prom),
            datastore=PrometheusDatastore(topo, prom),
            scm=GitHubSCM(settings.github_token, topo.repos()),
            deploys=deploys,
            flags=flags,
            topology=topo,
            actuator=actuator,
        )


def live_incident(
    service: str,
    name: str,
    summary: str,
    window_minutes: int = 45,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "incident_id": f"live_{int(now.timestamp())}",
        "alert": {
            "id": f"live_{int(now.timestamp())}",
            "service": service,
            "name": name,
            "severity": "high",
            "fired_at": (now - timedelta(minutes=3)).isoformat(timespec="seconds"),
            "summary": summary,
            "labels": labels or {},
        },
        "now": now.isoformat(timespec="seconds"),
        "window_minutes": window_minutes,
    }
