"""Incident lifecycle: create → run until approval → (later, any process) approve/reject → finish.

On disk, per incident:  .pz/incidents/<id>/meta.json · world.pkl · trace.jsonl
Graph checkpoints:       .pz/checkpoints.sqlite  (thread_id = incident id)
"""

from __future__ import annotations

import json
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from pager_zero.agent.graph import build_graph, msg_text, tool_output
from pager_zero.config import settings
from pager_zero.memory import get_store
from pager_zero.memory.store import incident_memory, recall_query

INCIDENTS_DIR = Path(".pz/incidents")


def _meta_path(incident_id: str) -> Path:
    return INCIDENTS_DIR / incident_id / "meta.json"


def load_meta(incident_id: str) -> dict[str, Any]:
    return json.loads(_meta_path(incident_id).read_text())


def save_meta(meta: dict[str, Any]) -> None:
    p = _meta_path(meta["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, default=str))


def list_incidents() -> list[dict[str, Any]]:
    if not INCIDENTS_DIR.exists():
        return []
    return sorted(
        (load_meta(p.name) for p in INCIDENTS_DIR.iterdir() if (p / "meta.json").exists()),
        key=lambda m: m["created"],
    )


def new_incident(scenario: str, seed: int, memory: bool = True) -> dict[str, Any]:
    iid = f"inc_{uuid.uuid4().hex[:8]}"
    meta = {
        "memory": memory,
        "id": iid,
        "scenario": scenario,
        "seed": seed,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "created",
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "world": str(INCIDENTS_DIR / iid / "world.pkl"),
    }
    save_meta(meta)
    return meta


def _server_args(meta: dict[str, Any], actions: bool) -> list[str]:
    args = [
        "-m",
        "pager_zero.cli",
        "mcp",
        "serve",
        "--scenario",
        meta["scenario"],
        "--seed",
        str(meta["seed"]),
        "--state",
        meta["world"],
    ]
    return args + (["--actions"] if actions else [])


def _client(meta: dict[str, Any], actions: bool) -> MultiServerMCPClient:
    return MultiServerMCPClient(
        {
            "pz": {
                "transport": "stdio",
                "command": sys.executable,
                "args": _server_args(meta, actions),
            }
        }
    )


@asynccontextmanager
async def _graph(meta: dict[str, Any], llm, trace):
    """Open the read-only MCP session + checkpointer, build the compiled graph."""
    ro = _client(meta, actions=False)
    async with ro.session("pz") as session:
        read_tools = await load_mcp_tools(session)

        async def executor(action: str, target: dict[str, Any]) -> dict[str, Any]:
            act = _client(meta, actions=True)
            async with act.session("pz") as s2:
                tools = {t.name: t for t in await load_mcp_tools(s2)}
                out = tool_output(await tools[action].ainvoke(target))
                trace({"event": "execute", "action": action, "target": target, "result": out})
                return out if isinstance(out, dict) else {"ok": True, "detail": str(out)}

        async def recall(context: dict[str, Any]) -> list[dict[str, Any]]:
            if not meta.get("memory", True):
                return []
            hits = get_store().recall(recall_query(context), k=settings.recall_k)
            trace({"event": "recall", "hits": [h.public() for h in hits]})
            return [h.public() for h in hits]

        settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
            graph = build_graph(
                llm, read_tools, executor, max_steps=settings.max_steps, recall=recall
            ).compile(checkpointer=saver)
            yield graph


class Tracer:
    """Appends JSONL to the incident's trace and renders progress to the console."""

    def __init__(self, meta: dict[str, Any], console) -> None:
        self.path = INCIDENTS_DIR / meta["id"] / "trace.jsonl"
        self.console = console
        self._seen_msgs = 0

    def __call__(self, event: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(
                json.dumps(
                    {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **event},
                    default=str,
                )
                + "\n"
            )

    def render(self, node: str, update: dict[str, Any], steps_total: int) -> None:
        c = self.console
        if node == "triage":
            a = update["context"]["alert"]
            c.print(
                f"[bold cyan][triage][/bold cyan] {a['name']} on [bold]{a['service']}[/bold] — {a['summary']}"
            )
            self({"event": "triage", "alert": a})
        elif node == "recall":
            hits = update.get("recalled") or []
            c.print(f"[cyan][recall][/cyan] {len(hits)} memory hit(s)")
            for h in hits:
                c.print(f"   • [{h['kind']}] {h['title']} (sim {h['similarity']})")
        elif node == "investigate":
            for m in update.get("messages", []):
                if isinstance(m, AIMessage):
                    for tc in m.tool_calls:
                        c.print(
                            f"[cyan][investigate][/cyan] → {tc['name']}({json.dumps(tc['args'])})"
                        )
                    if msg_text(m).strip() and not m.tool_calls:
                        c.print(f"[cyan][investigate][/cyan] {msg_text(m).strip()[:600]}")
                    self({"event": "llm", "tool_calls": m.tool_calls, "text": msg_text(m)})
        elif node == "tools":
            for m in update.get("messages", []):
                if isinstance(m, ToolMessage):
                    body = json.dumps(tool_output(m.content), default=str)
                    c.print(
                        f"[dim]   {update.get('steps', '?')}/{steps_total} {m.name} ← {body[:160].replace(chr(10), ' ')}…[/dim]"
                    )
                    self({"event": "tool_result", "tool": m.name, "result": body[:4000]})
        elif node == "diagnose":
            d = update["diagnosis"]
            c.print(
                f"[bold green][diagnose][/bold green] {d['family']} (conf {d['confidence']:.2f}): {d['root_cause']}"
            )
            for e in d["evidence"]:
                c.print(f"   • {e}")
            if d.get("ruled_out"):
                c.print(f"   ruled out: {'; '.join(d['ruled_out'])}")
            self({"event": "diagnosis", **d})
        elif node == "plan_fix":
            p = update.get("plan")
            if p:
                c.print(
                    f"[bold yellow][plan_fix][/bold yellow] {p['action']}({json.dumps(p['target'])})  risk={p['risk']}"
                )
                c.print(f"   why: {p['rationale']}\n   blast radius: {p['blast_radius']}")
                self({"event": "plan", **p})
            else:
                c.print("[yellow][plan_fix][/yellow] no confident fix — leaving for a human")
        elif node == "execute":
            r = update["execution"]
            c.print(f"[bold magenta][execute][/bold magenta] {r.get('detail', r)}")
        elif node == "verify":
            v = update["verification"]
            mark = "[green]✔ recovered[/green]" if v["recovered"] else "[red]✘ not recovered[/red]"
            c.print(f"[bold magenta][verify][/bold magenta] {v['note']}  {mark}")
            self({"event": "verification", **v})


async def run_until_approval(meta: dict[str, Any], console) -> dict[str, Any]:
    from pager_zero.llm import get_llm

    tracer = Tracer(meta, console)
    cfg = {"configurable": {"thread_id": meta["id"]}}
    async with _graph(meta, get_llm(), tracer) as graph:
        async for update in graph.astream({"incident_id": meta["id"]}, cfg, stream_mode="updates"):
            for node, payload in update.items():
                if node == "__interrupt__":
                    continue
                tracer.render(node, payload, settings.max_steps)
        state = await graph.aget_state(cfg)
    meta["status"] = state.values.get("status", "unknown")
    save_meta(meta)
    return state.values


async def resume(meta: dict[str, Any], decision: dict[str, Any], console) -> dict[str, Any]:
    from pager_zero.llm import get_llm

    tracer = Tracer(meta, console)
    tracer({"event": "approval", **decision})
    cfg = {"configurable": {"thread_id": meta["id"]}}
    async with _graph(meta, get_llm(), tracer) as graph:
        async for update in graph.astream(Command(resume=decision), cfg, stream_mode="updates"):
            for node, payload in update.items():
                if node != "__interrupt__":
                    tracer.render(node, payload, settings.max_steps)
        state = await graph.aget_state(cfg)
    meta["status"] = state.values.get("status", "unknown")
    save_meta(meta)
    _remember(meta, state.values)
    return state.values


def _remember(meta: dict[str, Any], values: dict[str, Any]) -> None:
    """Store the finished incident so future `recall` can find it (only real diagnoses)."""
    if not meta.get("memory", True) or not values.get("diagnosis"):
        return
    if values["diagnosis"].get("family") == "unknown":
        return
    get_store().remember(
        incident_memory(
            values["context"], values["diagnosis"], values.get("plan"), values.get("verification")
        )
    )


STATE_KEYS = (
    "incident_id",
    "context",
    "service_info",
    "recalled",
    "messages",
    "steps",
    "diagnosis",
    "plan",
    "approval",
    "execution",
    "verification",
    "status",
)


async def current_state(meta: dict[str, Any]) -> dict[str, Any]:
    """Read the latest checkpoint for an incident without building a graph."""
    settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        tup = await saver.aget_tuple({"configurable": {"thread_id": meta["id"]}})
        if tup is None:
            return {}
        values = tup.checkpoint.get("channel_values", {})
        return {k: values[k] for k in STATE_KEYS if k in values}
