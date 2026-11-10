from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from pager_zero.agent import prompts
from pager_zero.agent.schemas import ACTION_SIGNATURES, Diagnosis, FixPlan, Verification
from pager_zero.agent.state import AgentState

# A callable that performs ONE mutating action through an actions-enabled MCP session.
Executor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def msg_text(m: Any) -> str:
    """Provider-neutral text of a message (content may be str or a list of blocks)."""
    c = m.content
    if isinstance(c, str):
        return c
    return "".join(
        b.get("text", "") for b in c if isinstance(b, dict) and b.get("type", "text") == "text"
    )


def tool_output(out: Any) -> Any:
    """Normalise what an MCP tool returns (JSON string, content-block list, or dict) to data."""
    if isinstance(out, list):  # content blocks from the MCP adapter
        out = "".join(b.get("text", "") for b in out if isinstance(b, dict))
    if isinstance(out, str):
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out
    return out


def _tool_result(msg: ToolMessage) -> Any:
    return tool_output(msg.content)


async def _call(tools: dict[str, BaseTool], name: str, args: dict[str, Any]) -> Any:
    return tool_output(await tools[name].ainvoke(args))


_TYPE_HINT = {"str": "str", "int": "int", "auto": "the flag's previous value (bool/int/str)"}


def _catalogue() -> str:
    return "\n".join(
        f"- {a}({', '.join(f'{k}: {_TYPE_HINT[t]}' for k, t in sig.items())})"
        for a, sig in ACTION_SIGNATURES.items()
    )


def build_graph(
    llm: BaseChatModel,
    read_tools: list[BaseTool],
    executor: Executor,
    *,
    max_steps: int = 12,
    recall: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]] | None = None,
    verify_timeout_s: int = 0,
    verify_poll_s: int = 15,
):
    tools_by_name = {t.name: t for t in read_tools}
    llm_with_tools = llm.bind_tools(read_tools)

    # ------------------------------------------------------------ nodes

    async def triage(state: AgentState) -> AgentState:
        ctx = await _call(tools_by_name, "incident_context", {})
        svc = ctx["alert"]["service"]
        info = await _call(tools_by_name, "service_info", {"service": svc})
        return {"context": ctx, "service_info": info, "steps": 0, "status": "investigating"}

    async def recall_node(state: AgentState) -> AgentState:
        hits = await recall(state["context"]) if recall else []
        return {"recalled": hits}

    async def investigate(state: AgentState) -> AgentState:
        steps = state.get("steps", 0)
        system = prompts.INVESTIGATE_SYSTEM.format(
            max_steps=max_steps,
            steps=steps,
            context=json.dumps(state["context"], separators=(",", ":")),
            service_info=json.dumps(state["service_info"], separators=(",", ":")),
        )
        msgs: list[Any] = [SystemMessage(system)]
        if state.get("recalled"):
            msgs.append(
                HumanMessage(
                    "Similar past incidents (for hypotheses, not proof):\n"
                    + json.dumps(state["recalled"], indent=1)
                )
            )
        history = list(state.get("messages", []))
        if not history:
            history = [
                HumanMessage("Begin the investigation. Start by querying the alert's metric.")
            ]
        if steps >= max_steps:
            ai = await llm.ainvoke(msgs + history + [HumanMessage(prompts.BUDGET_EXHAUSTED)])
            ai = AIMessage(content=msg_text(ai))  # strip any stray tool calls
            return {"messages": history[len(state.get("messages", [])) :] + [ai]}
        ai = await llm_with_tools.ainvoke(msgs + history)
        new = history[len(state.get("messages", [])) :] + [ai]
        return {"messages": new}

    tool_node = ToolNode(read_tools, handle_tool_errors=True)

    async def tools(state: AgentState) -> AgentState:
        out = await tool_node.ainvoke({"messages": state["messages"]})
        n = len(state["messages"][-1].tool_calls)
        return {"messages": out["messages"], "steps": state.get("steps", 0) + n}

    def after_investigate(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "diagnose"

    async def diagnose(state: AgentState) -> AgentState:
        transcript = _transcript(state["messages"])
        structured = llm.with_structured_output(Diagnosis)
        d: Diagnosis = await structured.ainvoke(
            [
                SystemMessage(prompts.DIAGNOSE_SYSTEM),
                HumanMessage(
                    f"Alert:\n{json.dumps(state['context']['alert'])}\n\nTranscript:\n{transcript}"
                ),
            ]
        )
        return {"diagnosis": d.model_dump()}

    async def plan_fix(state: AgentState) -> AgentState:
        d = state["diagnosis"]
        if d["family"] == "unknown" or d["confidence"] < 0.3:
            return {"plan": None, "status": "unresolved"}
        structured = llm.with_structured_output(FixPlan)
        p: FixPlan = await structured.ainvoke(
            [
                SystemMessage(prompts.PLAN_SYSTEM.format(catalogue=_catalogue())),
                HumanMessage(
                    f"Diagnosis:\n{json.dumps(d, indent=1)}\n\nService topology:\n{json.dumps(state['service_info'], indent=1)}\n\n"
                    f"Investigation transcript (for exact names/versions):\n{_transcript(state['messages'])}"
                ),
            ]
        )
        return {"plan": {**p.model_dump(), "target": p.target()}, "status": "awaiting_approval"}

    def after_plan(state: AgentState) -> str:
        return "approval" if state.get("plan") else END

    async def approval(state: AgentState) -> AgentState:
        decision = interrupt({"diagnosis": state["diagnosis"], "plan": state["plan"]})
        if decision.get("decision") != "approve":
            return {"approval": decision, "status": "rejected"}
        return {"approval": decision, "status": "executing"}

    def after_approval(state: AgentState) -> str:
        return "execute" if state["status"] == "executing" else END

    async def execute(state: AgentState) -> AgentState:
        plan = state["plan"]
        result = await executor(plan["action"], plan["target"])
        return {"execution": result}

    async def _check_once(metric: str, labels: dict[str, str]) -> Verification:
        if metric == "consumer_lag":
            hist = await _call(
                tools_by_name, "lag_history", {"group": labels["group"], "minutes": 60}
            )
            series = [hist]
        else:
            out = await _call(
                tools_by_name, "query_metrics", {"metric": metric, "labels": labels, "minutes": 60}
            )
            series = out.get("items", []) if isinstance(out, dict) else out
        s = series[0] if series else None
        if not s or not s.get("points"):
            return Verification(
                metric=metric,
                labels=labels,
                before=0,
                after=0,
                baseline=0,
                recovered=False,
                note="no data",
            )
        before, after, baseline = s["max"], s["last"], s["first"]
        tol = 2.0 if metric == "consumer_lag" else 1.5
        recovered = after <= max(baseline * tol, baseline + 1e-9) and after < before * 0.5
        return Verification(
            metric=metric,
            labels=labels,
            before=before,
            after=after,
            baseline=baseline,
            recovered=recovered,
            note=f"{metric} peak {before:.1f} → now {after:.1f} (baseline {baseline:.1f})",
        )

    async def verify(state: AgentState) -> AgentState:
        """Code, not the model, decides whether the fix worked.

        A synthetic world heals instantly; a live system takes minutes (rate windows, restarts),
        so with `verify_timeout_s > 0` we re-check every `verify_poll_s` until recovered or timed out.
        """
        metric, labels = _key_metric(state["context"]["alert"])
        t0 = time.monotonic()
        waited = 0
        while True:
            v = await _check_once(metric, labels)
            elapsed = time.monotonic() - t0
            if v.recovered or elapsed >= verify_timeout_s:
                break
            waited += 1
            await asyncio.sleep(verify_poll_s)
        if waited:
            v.note += f" · watched {int(time.monotonic() - t0)} s"
        return {
            "verification": v.model_dump(),
            "status": "resolved" if v.recovered else "unresolved",
        }

    # ------------------------------------------------------------ wiring

    g = StateGraph(AgentState)
    g.add_node("triage", triage)
    g.add_node("recall", recall_node)
    g.add_node("investigate", investigate)
    g.add_node("tools", tools)
    g.add_node("diagnose", diagnose)
    g.add_node("plan_fix", plan_fix)
    g.add_node("approval", approval)
    g.add_node("execute", execute)
    g.add_node("verify", verify)

    g.add_edge(START, "triage")
    g.add_edge("triage", "recall")
    g.add_edge("recall", "investigate")
    g.add_conditional_edges(
        "investigate", after_investigate, {"tools": "tools", "diagnose": "diagnose"}
    )
    g.add_edge("tools", "investigate")
    g.add_edge("diagnose", "plan_fix")
    g.add_conditional_edges("plan_fix", after_plan, {"approval": "approval", END: END})
    g.add_conditional_edges("approval", after_approval, {"execute": "execute", END: END})
    g.add_edge("execute", "verify")
    g.add_edge("verify", END)
    return g


def _transcript(messages: list[Any]) -> str:
    lines = []
    for m in messages:
        if isinstance(m, AIMessage):
            if m.tool_calls:
                for tc in m.tool_calls:
                    lines.append(f"CALL {tc['name']}({json.dumps(tc['args'])})")
            if msg_text(m).strip():
                lines.append(f"AGENT: {msg_text(m).strip()}")
        elif isinstance(m, ToolMessage):
            body = json.dumps(_tool_result(m))[:1500]
            lines.append(f"RESULT {m.name}: {body}")
        elif isinstance(m, HumanMessage):
            lines.append(f"USER: {msg_text(m)}")
    return "\n".join(lines)


def _key_metric(alert: dict[str, Any]) -> tuple[str, dict[str, str]]:
    name, svc = alert["name"], alert["service"]
    if name == "ConsumerLagHigh":
        return "consumer_lag", {"group": alert.get("labels", {}).get("group", "")}
    if name == "HighErrorRate":
        return "http_error_rate", {"service": svc}
    return "http_p99_ms", {"service": svc}
