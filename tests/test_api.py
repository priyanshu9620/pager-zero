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
