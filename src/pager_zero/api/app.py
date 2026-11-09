from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from rich.console import Console

from pager_zero import __version__
from pager_zero.agent import runner
from pager_zero.config import settings
from pager_zero.synth import list_scenarios

app = FastAPI(
    title="Pager-Zero",
    version=__version__,
    description="LLM on-call agent: diagnoses an incident, prepares the fix, waits for a human.",
)

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    """The approval console: fire incidents, watch the investigation, approve or reject."""
    return FileResponse(STATIC / "index.html")


_quiet = Console(quiet=True)
_running: dict[str, asyncio.Task[Any]] = {}  # incident id -> background task


# ------------------------------------------------------------------ models


class CreateIncident(BaseModel):
    scenario: str = Field("bad-deploy", description="One of the synthetic scenario families.")
    seed: int = 1
    memory: bool = True


class Decision(BaseModel):
    note: str = ""


class AlertmanagerAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class AlertmanagerPayload(BaseModel):
    """Subset of the Alertmanager webhook schema we care about."""

    status: str = "firing"
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


# ------------------------------------------------------------------ helpers


def _meta_or_404(incident_id: str) -> dict[str, Any]:
    try:
        return runner.load_meta(incident_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"unknown incident {incident_id}") from e


def _view(meta: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    return {
        **meta,
        "running": meta["id"] in _running and not _running[meta["id"]].done(),
        "steps": state.get("steps"),
        "diagnosis": state.get("diagnosis"),
        "plan": state.get("plan"),
        "approval": state.get("approval"),
        "execution": state.get("execution"),
        "verification": state.get("verification"),
        "recalled": state.get("recalled"),
    }


async def _run_bg(meta: dict[str, Any]) -> None:
    try:
        await runner.run_until_approval(meta, _quiet)
    except Exception as e:  # noqa: BLE001
        meta["status"], meta["error"] = "error", f"{type(e).__name__}: {str(e)[:300]}"
        runner.save_meta(meta)


async def _resume_bg(meta: dict[str, Any], decision: dict[str, Any]) -> None:
    try:
        await runner.resume(meta, decision, _quiet)
    except Exception as e:  # noqa: BLE001
        meta["status"], meta["error"] = "error", f"{type(e).__name__}: {str(e)[:300]}"
        runner.save_meta(meta)


def _spawn(meta: dict[str, Any], coro: Any) -> None:
    task = asyncio.create_task(coro)
    _running[meta["id"]] = task


# ------------------------------------------------------------------ routes


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "llm": f"{settings.llm_provider}/{settings.llm_model}",
        "backend": settings.adapter_backend.value,
        "memory": settings.memory_backend,
    }


@app.get("/scenarios")
def scenarios() -> list[str]:
    return list_scenarios()


@app.post("/incidents", status_code=202)
async def create_incident(body: CreateIncident) -> dict[str, Any]:
    if body.scenario not in list_scenarios():
        raise HTTPException(400, f"unknown scenario {body.scenario!r}; see GET /scenarios")
    meta = runner.new_incident(body.scenario, body.seed, memory=body.memory)
    meta["status"] = "investigating"
    runner.save_meta(meta)
    _spawn(meta, _run_bg(meta))
    return _view(meta, None)


@app.get("/incidents")
def list_incidents() -> list[dict[str, Any]]:
    return runner.list_incidents()


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str) -> dict[str, Any]:
    meta = _meta_or_404(incident_id)
    state = await runner.current_state(meta) if meta["status"] != "created" else {}
    return _view(meta, state)


@app.get("/incidents/{incident_id}/trace")
def get_trace(incident_id: str) -> list[dict[str, Any]]:
    _meta_or_404(incident_id)
    path = Path(runner.INCIDENTS_DIR) / incident_id / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _decide(incident_id: str, decision: str, note: str) -> dict[str, Any]:
    meta = _meta_or_404(incident_id)
    if meta["status"] != "awaiting_approval":
        raise HTTPException(409, f"{incident_id} is {meta['status']}, not awaiting_approval")
    meta["status"] = "executing" if decision == "approve" else "rejected"
    runner.save_meta(meta)
    _spawn(meta, _resume_bg(meta, {"decision": decision, "note": note}))
    return _view(meta, None)


@app.post("/incidents/{incident_id}/approve", status_code=202)
async def approve(incident_id: str, body: Decision | None = None) -> dict[str, Any]:
    return await _decide(incident_id, "approve", (body or Decision()).note)


@app.post("/incidents/{incident_id}/reject", status_code=202)
async def reject(incident_id: str, body: Decision | None = None) -> dict[str, Any]:
    return await _decide(incident_id, "reject", (body or Decision()).note)


@app.post("/webhooks/alertmanager", status_code=202)
async def alertmanager(payload: AlertmanagerPayload, background: BackgroundTasks) -> dict[str, Any]:
    """Alertmanager webhook ingress.

    With the synthetic backend the alert must carry `pz_scenario` (and optionally `pz_seed`) labels
    so we know which world to build. A live backend (phase 7) would instead pass the alert through.
    """
    created = []
    for alert in payload.alerts:
        if alert.status != "firing":
            continue
        scenario = alert.labels.get("pz_scenario")
        if not scenario:
            if settings.adapter_backend.value == "synthetic":
                raise HTTPException(
                    400, "synthetic backend needs a `pz_scenario` label on the alert"
                )
            raise HTTPException(501, "live alert ingress not implemented yet")
        if scenario not in list_scenarios():
            raise HTTPException(400, f"unknown pz_scenario {scenario!r}")
        seed = int(alert.labels.get("pz_seed", "1"))
        meta = runner.new_incident(scenario, seed)
        meta["status"] = "investigating"
        meta["source"] = {
            "alertmanager": {"labels": alert.labels, "annotations": alert.annotations}
        }
        runner.save_meta(meta)
        _spawn(meta, _run_bg(meta))
        created.append(meta["id"])
    return {"created": created}
