# 01 · What is Pager-Zero (2-minute version)

**One line:** an AI on-call agent. You hand it an alert; it investigates like an engineer would (metrics, logs, recent deploys, Kafka lag, cache stats, feature flags), tells you the root cause with evidence, and proposes a concrete fix (rollback / revert flag / scale consumers…). It **never applies the fix on its own** — you approve from the CLI.

**Why "zero":** the goal is zero minutes between the page firing and having a first solid hypothesis + a ready-to-run fix.

**The problem it solves:** the first 10–30 minutes of most incidents are mechanical — open dashboards, correlate the spike with the last deploy, grep logs. That loop is tool-driven and repeatable; a bounded agent can do it while the human is still waking up.

**How it works, in one breath:**
alert → triage → recall similar past incidents (pgvector) → investigate in a step-capped loop using tools served over MCP → structured diagnosis → fix plan → **pause for human approval** → execute → verify.

**What makes it a real project, not a chatbot:**
- Tools are a proper **MCP server** — any MCP client (even Claude Code) can drive them.
- The agent is a **LangGraph state machine** with a hard step cap and a real `interrupt()` for approval.
- A **synthetic incident generator** fabricates whole consistent "worlds" (metrics + logs + deploys + Kafka + cache) with a hidden ground truth, so I can benchmark it on 20+ incidents reproducibly.
- **Pluggable adapters**: the agent only talks to interfaces; synthetic and real (Prometheus/Kafka/Redis/GitHub) backends plug in by config.
- **Model-agnostic**: provider is a config value; benchmarked on Claude and on a free-tier model.
