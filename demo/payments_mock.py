"""payments-mock: the downstream dependency. `docker compose stop payments-mock` is the outage."""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE = os.getenv("SERVICE_NAME", "payments-mock")
REQ = Histogram("http_request_duration_seconds", "latency", ["service", "route"])
REQS = Counter("http_requests_total", "requests", ["service", "route", "status"])
app = FastAPI(title=SERVICE)


@app.post("/charge")
def charge():
    t = time.perf_counter()
    time.sleep(0.01)
    REQ.labels(SERVICE, "charge").observe(time.perf_counter() - t)
    REQS.labels(SERVICE, "charge", "200").inc()
    return {"charged": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
