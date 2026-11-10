# Demo stack — a real system to break

Everything in the main README runs against a *generated* world. This folder is the opposite: a
small but real system — real HTTP service, real Postgres, real Redis, real Kafka (Redpanda), real
Prometheus and Alertmanager — that you damage on purpose, and Pager-Zero investigates through the
**same live adapters it would use on your infrastructure**. Nothing here is simulated: the p99 the
agent reads is `histogram_quantile` over real request histograms, and the fix it applies really
restarts a container, really runs `CONFIG SET` on Redis, really rescales the consumer group.

```
 loadgen ──► shop-api ──► Postgres (shop-db)        worker ×3 ◄── Redpanda topic `orders` ◄── loadgen
                │  └─────► Redis (offer-cache)
                └────────► payments-mock
 Prometheus scrapes shop-api, payments-mock, exporter (Kafka lag + Redis stats)
 Alertmanager ──webhook──► pz serve  (POST /webhooks/alertmanager)
```

## Run it

```bash
uv run pz demo up          # builds the images, starts 9 containers, writes demo/state/*
make demo-serve            # Pager-Zero on :8000 with ADAPTER_BACKEND=live ACTUATOR=demo
open http://127.0.0.1:8000/ui

uv run pz demo chaos bad-deploy        # or: flag-regression | cache-eviction | consumer-lag | downstream-outage
# ~1–2 min later Alertmanager pages pz serve → a ⚡ live incident appears → approve → watch Prometheus recover
uv run pz demo heal                    # undo every fault
uv run pz demo down                    # stop (add -v to drop data)
```

Prometheus http://localhost:9090 · alerts http://localhost:9090/alerts · Alertmanager http://localhost:9093 ·
shop-api http://localhost:8010/offers/42

## What each fault really does

| `pz demo chaos …` | real change | what the agent should find | the fix that really works |
|---|---|---|---|
| `bad-deploy` | shop-api restarted as `v1.5.0` with `SLOW_QUERY=1`: 15 extra queries per cache miss; entry appended to `deploys.json` | deploy at T, p99 and `db_qps` jump at T | `rollback_deploy(shop-api, v1.4.0)` → container restarted on the healthy version |
| `flag-regression` | `shop.offer_cache.ttl_seconds` → `0` in `flags.json` (hot-read every request) | flag history shows the change; `cache_hit_rate` → 0, `db_qps` ×10, no deploy | `set_flag(shop.offer_cache.ttl_seconds, 300)` |
| `cache-eviction` | `redis CONFIG SET maxmemory 2mb` (working set ≈ 4 MB) | `redis_evicted_keys_total` climbing, hit-rate collapse, `cache_stats` shows 2 MB cap | `set_cache_limit(offer-cache, ≥ 8)` |
| `consumer-lag` | worker group scaled 3 → 1 with 150 ms/message | `consumer_lag` growing, 1 member | `scale_consumers(order-events-consumer, ≥ 4)` |
| `downstream-outage` | `docker compose stop payments-mock` | `dependency_error_rate{payments-mock}` → 1, `http_error_rate` up, no deploy/flag change | `enable_fallback(shop-api, payments-mock)` → flips the fallback flag |

`hot-partition` has no live counterpart (it needs a datastore-specific hot-key exporter).

## How it maps onto the live adapters

| adapter | source in the demo |
|---|---|
| metrics | Prometheus recording rules in [`rules.yml`](rules.yml) produce the same names the agent knows: `http_p99_ms`, `db_qps`, `cache_hit_rate`, `dependency_error_rate`, `request_rate`, `http_error_rate` |
| messaging | Kafka admin API against Redpanda for current lag; `kafka_consumergroup_lag_sum` from [`exporter.py`](exporter.py) for history |
| cache | Redis `INFO` for the snapshot; `redis_*` gauges from the exporter for history |
| deploys / flags | `demo/state/deploys.json`, `demo/state/flags.json` — written by chaos and by the actuator |
| topology | `demo/state/topology.json` |
| actuator | `ACTUATOR=demo` → `pager_zero.demo.actuator.DemoActuator` (docker compose / redis-py / flag file) |

Verification is unchanged code: it re-queries the alert's key metric and compares to baseline and
peak — but on a live system that takes minutes, so `VERIFY_TIMEOUT_S=300` makes it poll every 15 s.

## Gotchas

- `pz serve` must listen on `0.0.0.0` (Alertmanager reaches it via `host.docker.internal`). `make demo-serve` does.
- The demo's Redis is on host port **6380** (a Homebrew `redis-server` often owns 6379).
- Alertmanager won't re-notify the same firing alert for `repeat_interval` (2 h). `pz demo heal`, wait for the alert to resolve, then chaos again.
- Rate windows are 1 min, so a fault shows up in `http_p99_ms` after ~30–60 s and the alert's `for: 45s` adds a bit more.
