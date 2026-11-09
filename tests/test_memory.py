import os

import pytest

from pager_zero.memory.runbooks import RUNBOOKS
from pager_zero.memory.store import (
    InMemoryStore,
    Memory,
    NullStore,
    PgVectorStore,
    incident_memory,
    recall_query,
)


def _ctx(
    name="HighLatencyP99", service="offer-service", summary="offer-service p99 > 800ms for 2m"
):
    return {"alert": {"name": name, "service": service, "summary": summary}}


def test_in_memory_store_recalls_relevant_runbook():
    store = InMemoryStore()
    for rb in RUNBOOKS:
        store.remember(rb)
    hits = store.recall(
        "cache evictions exploded and hit rate collapsed after maxmemory reached", k=2
    )
    assert hits and hits[0].meta["family"] == "cache-eviction"
    assert hits[0].score and hits[0].score > hits[1].score
    hits = store.recall("consumer group lag growing after pods OOM killed", k=1, kind="runbook")
    assert hits[0].meta["family"] == "consumer-lag"


def test_incident_memory_text_and_query():
    diag = {
        "family": "bad-deploy",
        "root_cause": "v1.60 N+1",
        "evidence": ["p99 4x", "deploy 11:06"],
    }
    plan = {
        "action": "rollback_deploy",
        "target": {"service": "checkout-api", "to_version": "v1.59"},
    }
    m = incident_memory(
        _ctx("HighLatencyP99", "checkout-api", "p99 > 800ms"), diag, plan, {"recovered": True}
    )
    assert m.kind == "incident" and "rollback_deploy" in m.text and "recovered" in m.text
    assert m.meta["fix_action"] == "rollback_deploy"
    assert "HighLatencyP99 on checkout-api" in recall_query(
        _ctx("HighLatencyP99", "checkout-api", "p99 > 800ms")
    )


def test_null_store_is_silent():
    s = NullStore()
    s.remember(Memory("incident", "t", "x"))
    assert s.recall("anything") == [] and s.count() == 0


def test_pgvector_store_degrades_when_db_is_down():
    s = PgVectorStore("postgresql+psycopg://pz:pz@127.0.0.1:1/pagerzero")  # nothing listens on :1
    s.remember(Memory("incident", "t", "x"))
    assert s.recall("x") == [] and s.count() == -1


@pytest.mark.skipif(
    not os.environ.get("PZ_TEST_PG"), reason="set PZ_TEST_PG=1 with docker compose up"
)
def test_pgvector_roundtrip():
    s = PgVectorStore("postgresql+psycopg://pz:pz@localhost:5432/pagerzero")
    before = s.count()
    s.remember(
        Memory(
            "incident",
            "test",
            "hot partition on offers_by_supplier key supplier:1 rate limited",
            {"family": "hot-partition"},
        )
    )
    assert s.count() == before + 1
    hits = s.recall("one shard hot after campaign, rate limit the key", k=1, kind="incident")
    assert hits and hits[0].meta["family"] == "hot-partition" and hits[0].score > 0.3
