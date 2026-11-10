"""loadgen: steady traffic so the metrics mean something.

~25 rps GET /offers/{1..N_OFFERS}, ~3 rps POST /checkout, ~20 msg/s to Kafka topic `orders`.
"""

from __future__ import annotations

import os
import random
import threading
import time

import httpx
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic

SHOP = os.getenv("SHOP_URL", "http://shop-api:8000")
BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
TOPIC = os.getenv("TOPIC", "orders")
N_OFFERS = int(os.getenv("N_OFFERS", "3000"))
OFFER_RPS = float(os.getenv("OFFER_RPS", "25"))
CHECKOUT_RPS = float(os.getenv("CHECKOUT_RPS", "3"))
MSG_RPS = float(os.getenv("MSG_RPS", "20"))


def ensure_topic() -> None:
    for _ in range(30):
        try:
            admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP)
            if TOPIC not in admin.list_topics():
                admin.create_topics([NewTopic(TOPIC, num_partitions=6, replication_factor=1)])
            admin.close()
            return
        except Exception as e:  # noqa: BLE001
            print(f"kafka not ready: {e}", flush=True)
            time.sleep(2)


def http_loop(path_fn, rps: float, method: str) -> None:
    client = httpx.Client(timeout=5)
    while True:
        t = time.perf_counter()
        try:
            if method == "GET":
                client.get(f"{SHOP}{path_fn()}")
            else:
                client.post(f"{SHOP}{path_fn()}")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(max(0.0, 1 / rps - (time.perf_counter() - t)))


def kafka_loop() -> None:
    ensure_topic()
    producer = KafkaProducer(bootstrap_servers=BOOTSTRAP)
    i = 0
    while True:
        producer.send(TOPIC, key=str(i % 6).encode(), value=f'{{"order":{i}}}'.encode())
        i += 1
        time.sleep(1 / MSG_RPS)


threads = [
    threading.Thread(target=http_loop, args=(lambda: f"/offers/{random.randint(1, N_OFFERS)}", OFFER_RPS, "GET"), daemon=True),
    threading.Thread(target=http_loop, args=(lambda: "/checkout", CHECKOUT_RPS, "POST"), daemon=True),
    threading.Thread(target=kafka_loop, daemon=True),
]
for th in threads:
    th.start()
print("loadgen running", flush=True)
while True:
    time.sleep(60)
