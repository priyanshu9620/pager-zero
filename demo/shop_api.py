"""shop-api: a small but real HTTP service the demo stack breaks on purpose.

GET /offers/{id}   read-through cache (Redis) in front of Postgres; TTL comes from a hot-reloaded flag
POST /checkout     calls payments-mock unless a fallback flag says to defer
GET /metrics       Prometheus exposition

Fault hooks (set by `pz demo chaos`, never by the agent):
  SLOW_QUERY=1   each cache miss does an N+1 fan-out of extra queries   (bad-deploy)
  APP_VERSION    reported in app_info{version} so a rollback is observable
Flags (demo/state/flags.json, read every request):
  shop.offer_cache.ttl_seconds     0 disables the cache → DB takes every read      (flag-regression)
  shop.fallback_payments.enabled   true → /checkout returns 202 without the dependency (enable_fallback)
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import httpx
import psycopg
import redis
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE = os.getenv("SERVICE_NAME", "shop-api")
VERSION = os.getenv("APP_VERSION", "v1.0.0")
SLOW_QUERY = os.getenv("SLOW_QUERY", "0") == "1"
FLAGS = Path(os.getenv("FLAGS_FILE", "/state/flags.json"))
PAYMENTS = os.getenv("PAYMENTS_URL", "http://payments-mock:8001")
N_OFFERS = int(os.getenv("N_OFFERS", "3000"))

REQ = Histogram(
    "http_request_duration_seconds",
    "request latency",
    ["service", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5),
)
REQS = Counter("http_requests_total", "requests", ["service", "route", "status"])
DBQ = Counter("db_queries_total", "database queries", ["service"])
CHIT = Counter("cache_hits_total", "cache hits", ["service", "cache"])
CMISS = Counter("cache_misses_total", "cache misses", ["service", "cache"])
DEP = Counter("dependency_requests_total", "dependency calls", ["service", "dependency", "outcome"])
INFO = Gauge("app_info", "build info", ["service", "version"])
INFO.labels(SERVICE, VERSION).set(1)
# pre-create the "bad" series at 0 so error-rate ratios exist (as 0) before anything fails
for _route, _status in (("checkout", "502"), ("offers", "500")):
    REQS.labels(SERVICE, _route, _status).inc(0)
for _outcome in ("ok", "error", "skipped"):
    DEP.labels(SERVICE, "payments-mock", _outcome).inc(0)
CHIT.labels(SERVICE, "offer-cache").inc(0)
CMISS.labels(SERVICE, "offer-cache").inc(0)

app = FastAPI(title=SERVICE)
r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=1)
pg = psycopg.connect(os.getenv("DATABASE_URL", "postgresql://shop:shop@shop-db:5432/shop"), autocommit=True)
http = httpx.Client(timeout=1.0)


def _flag(key: str, default):
    try:
        return json.loads(FLAGS.read_text())["values"].get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _seed() -> None:
    with pg.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS offers (id int PRIMARY KEY, price numeric, payload text)"
        )
        cur.execute("SELECT count(*) FROM offers")
        if cur.fetchone()[0] < N_OFFERS:
            cur.execute(
                "INSERT INTO offers SELECT g, (random()*1000)::numeric(10,2), repeat(md5(g::text), 32) "
                "FROM generate_series(1, %s) g ON CONFLICT DO NOTHING",
                (N_OFFERS,),
            )


_seed()


@app.middleware("http")
async def observe(request: Request, call_next):
    route = request.url.path.split("/")[1] or "root"
    t = time.perf_counter()
    resp = await call_next(request)
    if route != "metrics":
        REQ.labels(SERVICE, route).observe(time.perf_counter() - t)
        REQS.labels(SERVICE, route, str(resp.status_code)).inc()
    return resp


@app.get("/offers/{offer_id}")
def offer(offer_id: int):
    ttl = int(_flag("shop.offer_cache.ttl_seconds", 300))
    key = f"offer:{offer_id}"
    if ttl > 0:
        try:
            cached = r.get(key)
        except redis.RedisError:
            cached = None
        if cached:
            CHIT.labels(SERVICE, "offer-cache").inc()
            return json.loads(cached)
        CMISS.labels(SERVICE, "offer-cache").inc()
    with pg.cursor() as cur:
        DBQ.labels(SERVICE).inc()
        cur.execute("SELECT id, price, payload FROM offers WHERE id = %s", (offer_id,))
        row = cur.fetchone()
        if SLOW_QUERY:  # the regression: N+1 "related offers" lookups per miss
            for rid in random.sample(range(1, N_OFFERS + 1), 15):
                DBQ.labels(SERVICE).inc()
                cur.execute("SELECT price FROM offers WHERE id = %s", (rid,))
                cur.fetchone()
                time.sleep(0.02)
    if not row:
        return Response(status_code=404)
    doc = {"id": row[0], "price": float(row[1]), "payload": row[2]}
    if ttl > 0:
        try:
            r.set(key, json.dumps(doc), ex=ttl)
        except redis.RedisError:
            pass
    return doc


@app.post("/checkout")
def checkout():
    if _flag("shop.fallback_payments.enabled", False):
        DEP.labels(SERVICE, "payments-mock", "skipped").inc()
        return Response(status_code=202, content='{"status":"deferred"}', media_type="application/json")
    try:
        resp = http.post(f"{PAYMENTS}/charge", json={"amount": 10})
        resp.raise_for_status()
        DEP.labels(SERVICE, "payments-mock", "ok").inc()
        return {"status": "paid"}
    except Exception:  # noqa: BLE001
        DEP.labels(SERVICE, "payments-mock", "error").inc()
        return Response(status_code=502, content='{"error":"payments unavailable"}', media_type="application/json")


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": VERSION}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
