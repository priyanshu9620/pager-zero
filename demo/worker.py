"""order-worker: Kafka consumer group `order-events-consumer` on topic `orders`.

WORKER_SLEEP_MS controls per-message work. `pz demo chaos consumer-lag` raises it and scales the
group to one member so lag grows; `scale_consumers` (the agent's fix) adds members back.
"""

from __future__ import annotations

import os
import time

from kafka import KafkaConsumer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
GROUP = os.getenv("GROUP", "order-events-consumer")
TOPIC = os.getenv("TOPIC", "orders")
SLEEP = int(os.getenv("WORKER_SLEEP_MS", "5")) / 1000

for attempt in range(30):
    try:
        consumer = KafkaConsumer(
            TOPIC,
            bootstrap_servers=BOOTSTRAP,
            group_id=GROUP,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            auto_commit_interval_ms=1000,
        )
        break
    except Exception as e:  # noqa: BLE001
        print(f"kafka not ready ({e}); retry {attempt}", flush=True)
        time.sleep(2)
else:
    raise SystemExit("kafka never became ready")

print(f"worker up group={GROUP} sleep={SLEEP * 1000:.0f}ms", flush=True)
for _msg in consumer:
    time.sleep(SLEEP)
