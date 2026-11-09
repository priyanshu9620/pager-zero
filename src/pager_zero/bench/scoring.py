"""Scoring is deterministic and conservative — no LLM judge.

root_cause_ok : diagnosed family == planted family
culprit_ok    : the diagnosis text names the specific culprit (version/commit/flag/group/cache/key/dependency)
fix_ok        : proposed action+target satisfies GroundTruth.matches (exact names; ≥/≤ for sizes)
verified      : after executing, the alert's key metric actually recovered
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from pager_zero.synth.world import GroundTruth

CULPRIT_KEYS = ("deploy", "commit", "flag", "group", "cache", "key", "dependency", "table", "topic")


@dataclass
class Score:
    scenario: str
    seed: int
    incident_id: str
    status: str
    steps: int
    duration_s: float
    input_tokens: int
    output_tokens: int
    root_cause_ok: bool
    culprit_ok: bool
    fix_ok: bool
    verified: bool
    diagnosed_family: str | None
    proposed: str | None
    expected: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def culprit_named(gt: GroundTruth, diagnosis: dict[str, Any] | None) -> bool:
    if not diagnosis:
        return False
    text = " ".join([diagnosis.get("root_cause", ""), *diagnosis.get("evidence", [])]).lower()
    wanted = [
        str(v).lower()
        for k, v in gt.culprit.items()
        if k in CULPRIT_KEYS and isinstance(v, str | int) and len(str(v)) >= 3
    ]
    return bool(wanted) and all(re.search(re.escape(w), text) for w in wanted)


def score(
    scenario: str,
    seed: int,
    meta: dict[str, Any],
    gt: GroundTruth,
    state: dict[str, Any],
    duration_s: float,
    error: str | None = None,
) -> Score:
    diag = state.get("diagnosis")
    plan = state.get("plan")
    ver = state.get("verification") or {}
    usage_in = usage_out = 0
    for m in state.get("messages", []) or []:
        u = getattr(m, "usage_metadata", None) or {}
        usage_in += int(u.get("input_tokens", 0) or 0)
        usage_out += int(u.get("output_tokens", 0) or 0)
    return Score(
        scenario=scenario,
        seed=seed,
        incident_id=meta["id"],
        status=state.get("status", "error"),
        steps=int(state.get("steps", 0) or 0),
        duration_s=round(duration_s, 1),
        input_tokens=usage_in,
        output_tokens=usage_out,
        root_cause_ok=bool(diag) and diag.get("family") == gt.family,
        culprit_ok=culprit_named(gt, diag),
        fix_ok=bool(plan) and gt.matches(plan["action"], plan["target"]),
        verified=bool(ver.get("recovered")),
        diagnosed_family=diag.get("family") if diag else None,
        proposed=f"{plan['action']}({plan['target']})" if plan else None,
        expected=f"{gt.fix_action}({gt.fix_target})",
        error=error,
    )
