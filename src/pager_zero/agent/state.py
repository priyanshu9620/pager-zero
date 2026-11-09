from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    incident_id: str
    context: dict[str, Any]  # incident_context tool output
    service_info: dict[str, Any]
    recalled: list[dict[str, Any]]  # similar past incidents (phase 4)
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # tool calls made so far
    diagnosis: dict[str, Any] | None
    plan: dict[str, Any] | None
    approval: dict[str, Any] | None  # {"decision": "approve"|"reject", "note": str}
    execution: dict[str, Any] | None
    verification: dict[str, Any] | None
    status: Literal[
        "investigating", "awaiting_approval", "executing", "resolved", "rejected", "unresolved"
    ]
