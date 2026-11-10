"""exporter: the two exporters the live adapters expect, in one tiny process.

  kafka_consumergroup_lag_sum{consumergroup}      (what kafka_exporter would publish)
  redis_keyspace_hit_ratio{instance}              (what redis_exporter + a recording rule would)
  redis_memory_used_bytes{instance}  redis_maxmemory_bytes{instance}  redis_evicted_keys_total{instance}
"""

from __future__ import annotations

import os
import time

import redis
from kafka import KafkaAdminClient, KafkaConsumer
from prometheus_client import Gauge, start_http_server

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
GROUPS = os.getenv("GROUPS", "order-events-consumer").split(",")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_NAME = os.getenv("CACHE_NAME", "offer-cache")

LAG = Gauge("kafka_consumergroup_lag_sum", "total lag", ["consumergroup"])
MEMBERS = Gauge("kafka_consumergroup_members", "members", ["consumergroup"])
HIT = Gauge("redis_keyspace_hit_ratio", "hit ratio over the last interval", ["instance"])
USED = Gauge("redis_memory_used_bytes", "used memory", ["instance"])
MAXM = Gauge("redis_maxmemory_bytes", "maxmemory", ["instance"])
EVICT = Gauge("redis_evicted_keys_total", "evicted keys", ["instance"])

start_http_server(9100)
r = redis.Redis.from_url(REDIS_URL, socket_timeout=2)
admin = consumer = None
prev_hits = prev_misses = None

while True:
    try:
        if admin is None:
            admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP)
            consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP)
        for g in GROUPS:
            committed = admin.list_group_offsets({g: None}).get(g, {})
            if committed:
                ends = consumer.end_offsets(list(committed))
                LAG.labels(g).set(sum(max(0, ends[tp] - om.offset) for tp, om in committed.items()))
            else:
                LAG.labels(g).set(0)
            desc = admin.describe_groups([g]).get(g) or {}
            MEMBERS.labels(g).set(len(desc.get("members", [])))
    except Exception as e:  # noqa: BLE001
        print(f"kafka: {e}", flush=True)
        admin = consumer = None
    try:
        info = r.info()
        hits, misses = int(info.get("keyspace_hits", 0)), int(info.get("keyspace_misses", 0))
        if prev_hits is not None:
            dh, dm = hits - prev_hits, misses - prev_misses
            HIT.labels(CACHE_NAME).set(dh / (dh + dm) if dh + dm else 1.0)
        prev_hits, prev_misses = hits, misses
        USED.labels(CACHE_NAME).set(int(info.get("used_memory", 0)))
        MAXM.labels(CACHE_NAME).set(int(info.get("maxmemory", 0)))
        EVICT.labels(CACHE_NAME).set(int(info.get("evicted_keys", 0)))
    except Exception as e:  # noqa: BLE001
        print(f"redis: {e}", flush=True)
    time.sleep(5)
