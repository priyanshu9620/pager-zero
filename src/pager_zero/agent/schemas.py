"""Structured outputs the agent must produce. Kept provider-neutral: no dict-typed fields
(several providers reject open `additionalProperties` in tool/response schemas)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pager_zero.adapters.base import ACTION_NAMES

Family = Literal[
    "bad-deploy",
    "flag-regression",
    "consumer-lag",
    "cache-eviction",
    "hot-partition",
    "downstream-outage",
    "unknown",
]

# Which arguments each action takes, and how to coerce the string values the model emits.
ACTION_SIGNATURES: dict[str, dict[str, str]] = {
    "rollback_deploy": {"service": "str", "to_version": "str"},
    "set_flag": {"key": "str", "value": "auto"},
    "scale_consumers": {"group": "str", "members": "int"},
    "restart_consumer_group": {"group": "str"},
    "set_cache_limit": {"name": "str", "max_memory_mb": "int"},
    "rate_limit_key": {"table": "str", "key": "str", "rps": "int"},
    "enable_fallback": {"service": "str", "dependency": "str"},
}
assert set(ACTION_SIGNATURES) == set(ACTION_NAMES)


class Diagnosis(BaseModel):
    family: Family = Field(description="The incident family that best explains the evidence.")
    root_cause: str = Field(
        description="One or two sentences: what happened and why it caused the alert."
    )
    confidence: float = Field(ge=0, le=1, description="0..1 confidence in the root cause.")
    evidence: list[str] = Field(
        description="Specific tool observations that support the cause (with numbers/times)."
    )
    ruled_out: list[str] = Field(
        default_factory=list,
        description="Hypotheses checked and rejected, with the observation that rejected them.",
    )


class Arg(BaseModel):
    key: str
    value: str = Field(
        description="Value as a string; numbers/booleans are coerced from the action signature."
    )


class FixPlan(BaseModel):
    action: Literal[
        "rollback_deploy",
        "set_flag",
        "scale_consumers",
        "restart_consumer_group",
        "set_cache_limit",
        "rate_limit_key",
        "enable_fallback",
    ]
    args: list[Arg] = Field(
        description="Arguments for the action, exactly the keys in its signature."
    )
    rationale: str
    risk: Literal["low", "medium", "high"]
    blast_radius: str = Field(description="What else this action can affect.")
    alternatives: list[str] = Field(default_factory=list)

    def target(self) -> dict[str, Any]:
        sig = ACTION_SIGNATURES[self.action]
        out: dict[str, Any] = {}
        for a in self.args:
            if a.key not in sig:
                continue
            out[a.key] = _coerce(a.value, sig[a.key])
        return out


def _coerce(v: str, kind: str) -> Any:
    s = v.strip()
    if kind == "int":
        return int(float(s))
    if kind == "auto":
        low = s.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            return s
    return s


class Verification(BaseModel):
    metric: str
    labels: dict[str, str]
    before: float
    after: float
    baseline: float
    recovered: bool
    note: str
