"""API tests with the agent runner monkeypatched (no model calls)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from pager_zero.agent import runner
from pager_zero.api import app as api_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "INCIDENTS_DIR", tmp_path / "incidents")
    api_module._running.clear()
    return AsyncClient(transport=ASGITransport(app=api_module.app), base_url="http://t")


def _fake_runner(monkeypatch, state_after_run: dict, state_after_resume: dict):
    async def run_until_approval(meta, console):
        meta["status"] = state_after_run["status"]
        runner.save_meta(meta)
        return state_after_run

    async def resume(meta, decision, console):
        meta["status"] = state_after_resume["status"]
        runner.save_meta(meta)
        return state_after_resume

    async def current_state(meta):
        return state_after_resume if meta["status"] in ("resolved", "rejected") else state_after_run

    monkeypatch.setattr(runner, "run_until_approval", run_until_approval)
    monkeypatch.setattr(runner, "resume", resume)
    monkeypatch.setattr(runner, "current_state", current_state)


async def _settle():
    for t in list(api_module._running.values()):
        if not t.done():
            await asyncio.wait_for(t, 5)


async def test_ui_is_served(client):
    async with client as c:
        r = await c.get("/ui")
        assert r.status_code == 200 and "Pager-Zero" in r.text and "/incidents" in r.text
        r = await c.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/ui"


async def test_health_and_scenarios(client):
    async with client as c:
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"]
        assert "bad-deploy" in (await c.get("/scenarios")).json()


async def test_incident_lifecycle(client, monkeypatch):
    plan = {
        "action": "rollback_deploy",
        "target": {"service": "checkout-api", "to_version": "v1.59"},
    }
    _fake_runner(
        monkeypatch,
        {
            "status": "awaiting_approval",
            "steps": 5,
            "diagnosis": {"family": "bad-deploy"},
            "plan": plan,
        },
        {
            "status": "resolved",
            "steps": 5,
            "diagnosis": {"family": "bad-deploy"},
            "plan": plan,
            "verification": {"recovered": True},
        },
    )
    async with client as c:
        r = await c.post("/incidents", json={"scenario": "bad-deploy", "seed": 42})
        assert r.status_code == 202
        iid = r.json()["id"]
        await _settle()

        r = await c.get(f"/incidents/{iid}")
        assert r.json()["status"] == "awaiting_approval" and r.json()["plan"] == plan

        r = await c.post(f"/incidents/{iid}/approve", json={"note": "ok"})
        assert r.status_code == 202
        await _settle()

        r = await c.get(f"/incidents/{iid}")
        assert r.json()["status"] == "resolved" and r.json()["verification"]["recovered"]

        # second approve is a conflict
        assert (await c.post(f"/incidents/{iid}/approve")).status_code == 409
        assert (await c.get("/incidents")).json()[0]["id"] == iid
        assert (await c.get("/incidents/nope")).status_code == 404
        assert (await c.post("/incidents", json={"scenario": "nope"})).status_code == 400


async def test_alertmanager_webhook(client, monkeypatch):
    _fake_runner(monkeypatch, {"status": "unresolved"}, {"status": "unresolved"})
    async with client as c:
        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighLatencyP99",
                        "pz_scenario": "hot-partition",
                        "pz_seed": "3",
                    },
                }
            ],
        }
        r = await c.post("/webhooks/alertmanager", json=payload)
        assert r.status_code == 202 and len(r.json()["created"]) == 1
        await _settle()
        meta = runner.load_meta(r.json()["created"][0])
        assert (
            meta["scenario"] == "hot-partition"
            and meta["seed"] == 3
            and "alertmanager" in meta["source"]
        )
        # missing label → 400 on synthetic backend
        r = await c.post(
            "/webhooks/alertmanager", json={"alerts": [{"labels": {"alertname": "x"}}]}
        )
        assert r.status_code == 400


async def test_blind_incident_hides_then_reveals(client, monkeypatch):
    plan = {"action": "set_cache_limit", "target": {"name": "offer-cache", "max_memory_mb": 99999}}
    _fake_runner(
        monkeypatch,
        {"status": "awaiting_approval", "diagnosis": {"family": "cache-eviction"}, "plan": plan},
        {
            "status": "resolved",
            "diagnosis": {"family": "cache-eviction", "root_cause": "x", "evidence": []},
            "plan": plan,
            "verification": {"recovered": True},
        },
    )
    async with client as c:
        r = await c.post("/incidents", json={"scenario": "random"})
        assert r.status_code == 202
        body = r.json()
        iid = body["id"]
        assert body["blind"] is True and body["scenario"] == "hidden" and body["seed"] is None
        await _settle()

        listed = (await c.get("/incidents")).json()[0]
        assert listed["scenario"] == "hidden" and listed["seed"] is None
        got = (await c.get(f"/incidents/{iid}")).json()
        assert got["scenario"] == "hidden" and got["truth"] is None

        await c.post(f"/incidents/{iid}/approve")
        await _settle()
        got = (await c.get(f"/incidents/{iid}")).json()
        real = runner.load_meta(iid)
        assert got["scenario"] == real["scenario"] and got["seed"] == real["seed"]
        t = got["truth"]
        assert t["planted"]["family"] == real["scenario"]
        assert t["root_cause_ok"] is (real["scenario"] == "cache-eviction")
        assert t["fix_ok"] is (
            real["scenario"] == "cache-eviction"
            and t["planted"]["fix"]["target"]["name"] == "offer-cache"
        )


async def test_alertmanager_live_ingress_and_dedupe(client, monkeypatch):
    from pager_zero.config import AdapterBackend, settings

    monkeypatch.setattr(settings, "adapter_backend", AdapterBackend.live)
    _fake_runner(monkeypatch, {"status": "awaiting_approval"}, {"status": "resolved"})
    alert = {
        "status": "firing",
        "labels": {"alertname": "HighLatencyP99", "service": "shop-api", "severity": "high"},
        "annotations": {"summary": "shop-api p99 > 400ms"},
    }
    async with client as c:
        r = await c.post("/webhooks/alertmanager", json={"status": "firing", "alerts": [alert]})
        assert r.status_code == 202 and len(r.json()["created"]) == 1
        iid = r.json()["created"][0]
        meta = runner.load_meta(iid)
        assert meta["backend"] == "live" and meta["alert"]["service"] == "shop-api"
        assert meta["alert"]["summary"] == "shop-api p99 > 400ms"
        await _settle()
        # the same alert again while the incident is open → not a second incident
        r = await c.post("/webhooks/alertmanager", json={"status": "firing", "alerts": [alert]})
        assert r.json()["created"] == [] and r.json()["already_open"] == [iid]
        got = (await c.get(f"/incidents/{iid}")).json()
        assert got["scenario"] == "live" and got["truth"] is None
        # no service label → 400
        bad = {**alert, "labels": {"alertname": "X"}}
        r = await c.post("/webhooks/alertmanager", json={"status": "firing", "alerts": [bad]})
        assert r.status_code == 400
