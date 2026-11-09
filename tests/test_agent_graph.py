"""Offline test of the LangGraph agent with a scripted fake LLM.

Proves: triage → investigate loop with real MCP tools (in-process) → step cap → diagnose →
plan → interrupt → resume → execute → verify, with the world actually healing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from pager_zero.agent.graph import build_graph
from pager_zero.agent.schemas import Diagnosis, FixPlan
from pager_zero.mcp_server.server import synthetic_server
from pager_zero.synth import generate


class ScriptedLLM(BaseChatModel):
    """Emits a fixed sequence of AIMessages; structured outputs come from `structured`."""

    script: list[AIMessage]
    structured: dict[str, Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        obj = self.structured[schema.__name__]

        class _R:
            async def ainvoke(self_inner, _msgs):
                return obj

        return _R()


async def _tools_from_server(srv):
    """Wrap FastMCP tools as LangChain tools without spawning a process."""
    out = []
    for t in await srv.list_tools():
        name = t.name

        async def _run(_name=name, **kwargs):
            blocks, structured = await srv.call_tool(_name, kwargs)
            if isinstance(structured, dict):
                return json.dumps(
                    structured.get("result", structured)
                    if set(structured) == {"result"}
                    else structured
                )
            return "".join(getattr(b, "text", "") for b in blocks)

        out.append(
            StructuredTool.from_function(
                coroutine=_run,
                name=name,
                description=t.description or name,
                args_schema=t.inputSchema,
            )
        )
    return out


def _tc(name: str, args: dict, i: int) -> dict:
    return {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_full_run_bad_deploy(tmp_path, decision):
    scenario, seed = "bad-deploy", 42
    state = tmp_path / "world.pkl"
    ro = synthetic_server(scenario, seed, state, actions_enabled=False)
    act = synthetic_server(scenario, seed, state, actions_enabled=True)
    read_tools = await _tools_from_server(ro)
    act_tools = {t.name: t for t in await _tools_from_server(act)}
    gt = generate(scenario, seed).ground_truth

    script = [
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "query_metrics",
                    {"metric": "http_p99_ms", "labels": {"service": "checkout-api"}},
                    1,
                )
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                _tc("recent_deploys", {"service": "checkout-api"}, 2),
                _tc("search_logs", {"service": "checkout-api", "query": "error"}, 3),
            ],
        ),
        AIMessage(content="Deploy v1.60 caused it."),
    ]
    diag = Diagnosis(
        family="bad-deploy",
        root_cause="v1.60 regression",
        confidence=0.9,
        evidence=["p99 4x after 11:06"],
        ruled_out=["no flag change"],
    )
    plan = FixPlan(
        action="rollback_deploy",
        args=[
            {"key": "service", "value": "checkout-api"},
            {"key": "to_version", "value": gt.fix_target["to_version"]},
        ],
        rationale="revert",
        risk="low",
        blast_radius="checkout-api only",
    )
    llm = ScriptedLLM(script=script, structured={"Diagnosis": diag, "FixPlan": plan})

    executed: list[tuple[str, dict]] = []

    async def executor(action: str, target: dict) -> dict:
        executed.append((action, target))
        out = await act_tools[action].ainvoke(target)
        return json.loads(out)

    graph = build_graph(llm, read_tools, executor, max_steps=12).compile(
        checkpointer=InMemorySaver()
    )
    cfg = {"configurable": {"thread_id": "t1"}}
    result = await graph.ainvoke({"incident_id": "t1"}, cfg)

    assert result["status"] == "awaiting_approval"
    assert result["steps"] == 3
    assert result["plan"]["target"] == {
        "service": "checkout-api",
        "to_version": gt.fix_target["to_version"],
    }
    assert "__interrupt__" in result
    assert "rollback_deploy" not in {t.name for t in read_tools}  # investigation truly read-only

    final = await graph.ainvoke(Command(resume={"decision": decision, "note": "test"}), cfg)
    if decision == "reject":
        assert final["status"] == "rejected" and not executed
    else:
        assert executed == [("rollback_deploy", result["plan"]["target"])]
        assert final["execution"]["ok"]
        assert final["verification"]["recovered"], final["verification"]
        assert final["status"] == "resolved"


async def test_step_cap_forces_diagnosis(tmp_path):
    ro = synthetic_server("hot-partition", 1, None, actions_enabled=False)
    read_tools = await _tools_from_server(ro)
    # an LLM that would call tools forever
    forever = [
        AIMessage(
            content="", tool_calls=[_tc("query_metrics", {"metric": "http_rps", "labels": {}}, i)]
        )
        for i in range(50)
    ]
    diag = Diagnosis(family="unknown", root_cause="?", confidence=0.1, evidence=[])
    llm = ScriptedLLM(script=forever, structured={"Diagnosis": diag, "FixPlan": None})

    async def executor(a, t):
        raise AssertionError("must not execute")

    graph = build_graph(llm, read_tools, executor, max_steps=4).compile(
        checkpointer=InMemorySaver()
    )
    result = await graph.ainvoke({"incident_id": "t2"}, {"configurable": {"thread_id": "t2"}})
    assert result["steps"] == 4
    assert result["diagnosis"]["family"] == "unknown"
    assert result["plan"] is None and result["status"] == "unresolved"


async def test_current_state_reads_sqlite_checkpoint(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from pager_zero.agent import runner
    from pager_zero.config import settings

    monkeypatch.setattr(settings, "checkpoint_db", tmp_path / "cp.sqlite")
    ro = synthetic_server("bad-deploy", 42, None, actions_enabled=False)
    read_tools = await _tools_from_server(ro)
    diag = Diagnosis(family="bad-deploy", root_cause="x", confidence=0.9, evidence=[])
    plan = FixPlan(
        action="rollback_deploy",
        args=[{"key": "service", "value": "checkout-api"}, {"key": "to_version", "value": "v1.59"}],
        rationale="r",
        risk="low",
        blast_radius="b",
    )
    llm = ScriptedLLM(
        script=[AIMessage(content="done")], structured={"Diagnosis": diag, "FixPlan": plan}
    )

    async def executor(a, t):
        return {"ok": True}

    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        graph = build_graph(llm, read_tools, executor, max_steps=3).compile(checkpointer=saver)
        await graph.ainvoke({"incident_id": "t3"}, {"configurable": {"thread_id": "t3"}})

    st = await runner.current_state({"id": "t3"})
    assert st["status"] == "awaiting_approval" and st["plan"]["action"] == "rollback_deploy"
    assert st["diagnosis"]["family"] == "bad-deploy" and "messages" in st
    assert await runner.current_state({"id": "nope"}) == {}
