# 02 · Architecture (what talks to what)

```
pz CLI / FastAPI
      │ start incident
      ▼
LangGraph agent  ── triage → recall → investigate ⟲(≤12 steps) → diagnose → plan_fix → ⏸ approval → execute → verify
      │ tools (via langchain-mcp-adapters)              │ pgvector
      ▼                                                 ▼
MCP tool server (FastMCP)                      Postgres + pgvector
  read tools: query_metrics, search_logs,      (past incidents, runbooks)
    consumer_lag, cache_stats, recent_deploys,
    recent_commits, get_diff, get_flag, …
  mutating tools (gated): rollback_deploy,
    revert_flag, scale_consumers, …
      │ Protocols (adapters/base.py)
      ▼
adapters: synthetic.py │ prometheus.py │ kafka.py │ redis.py │ github.py
                ▲
   synth/ generator: scenario + seed → World (+ hidden GroundTruth)
```

## The five pieces and the one sentence each
1. **Synthetic world** (`synth/`): a scenario generator builds a self-consistent fake universe — services, a deploy at 14:02, a latency series that jumps at 14:03, log lines, Kafka lag, cache hit-rate, flag history — plus the hidden answer (cause + fix). Deterministic by seed.
2. **Adapters** (`adapters/`): Python Protocols (`MetricsAdapter.query_range`, `MessagingAdapter.consumer_lag`, …). `synthetic.py` serves the world; the real ones hit Prometheus/Kafka/Redis/GitHub APIs. Chosen by `ADAPTER_BACKEND`.
3. **MCP server** (`mcp_server/`): wraps adapter methods as MCP tools. Read tools vs mutating tools are tagged separately.
4. **Agent** (`agent/`): LangGraph graph; state carries the incident, evidence list, step counter, diagnosis, plan. `interrupt()` before `execute`. Checkpointed to SQLite so `pz approve` can resume a paused run later.
5. **Memory + bench** (`memory/`, `bench/`): resolved incidents are embedded (local MiniLM) into pgvector and recalled at the start of the next incident; the bench runner generates N incidents, auto-approves, and scores root-cause + fix against ground truth.

## Why these choices (short)
- **LangGraph over a plain loop:** need a real pause/resume (approval) and a step cap as graph logic, not `while True`.
- **MCP over in-process functions:** decouples tools from the agent; any MCP client can reuse them; it's the industry standard for tool exposure now.
- **Synthetic over real incidents:** reproducible, labelled, infinite; demo works offline in 30 s.
- **Protocols over inheritance:** adapters are structural — a new backend is just a class with the right methods.
- **pgvector over a vector SaaS:** one Postgres container, no key, SQL-queryable.
