# 01 — What Pager-Zero is

## The 30-second version

Pager-Zero is an on-call agent. When an alert fires, it does the first fifteen minutes of an
incident the way an engineer would — reads the metrics, checks what deployed, what flag changed,
whether a consumer group is lagging, whether a cache is evicting, whether a dependency is failing —
then writes a diagnosis with evidence, proposes **one** concrete fix, and **stops**. A human
approves or rejects. Only after approval does it execute, and then it checks, with code, that the
metric actually recovered.

## The 2-minute version

Most incidents start the same way: someone wakes up, opens five dashboards, lines the spike up
with the last deploy, greps the logs, checks lag. That loop is mechanical and tool-driven, which
makes it a good fit for an LLM agent with tools — as long as the agent is **bounded** (hard cap on
tool calls), **can't act on its own** (mutating tools aren't even registered until a human
approves), and **isn't trusted to grade itself** (verification is deterministic code reading the
metric again).

I built it in three layers:

1. **Tools.** An MCP server exposes 16 read-only tools (metrics, logs, deploys, commits, diffs,
   flags, consumer lag, cache stats, hot keys, partition latency, dependency health, topology) and
   7 mutating ones (rollback, set flag, scale consumers, restart group, raise cache limit, rate-limit
   a key, enable fallback). Any MCP client — Claude Code, Cursor, my agent — can drive them.
2. **Agent.** A LangGraph state machine: triage → recall (RAG over past incidents + runbooks) →
   investigate loop (≤ 12 tool calls) → diagnose → plan fix → *interrupt for approval* → execute →
   verify. Checkpointed to SQLite so "approve" can happen hours later from another process. Any
   LLM: one `init_chat_model` factory, no provider-specific features.
3. **Backends.** The tools talk to adapters behind Python Protocols. Two implementations: a
   **synthetic world** (a generator plants one cause with a hidden ground truth, so accuracy is
   measurable) and a **live** backend (Prometheus, Loki, Kafka, Redis, GitHub, deploy/flag logs).

I measured it: 20 generated incidents across 6 failure families, scored against the hidden truth
with no LLM judge — **95 % root cause, 95 % correct fix, 95 % verified recovery** on a free Gemini
model, ~8 tool calls and ~34 s per incident. And I proved the live path on a small **real** system
in Docker: inject a bad deploy, Prometheus alerts, Alertmanager pages the agent, it diagnoses the
deploy, I approve, it really rolls the container back, and the real p99 comes down.

## The 5-minute version — add these

- **Blind mode.** Picking "cache-eviction" from a menu and watching the agent "find" cache-eviction
  looks rigged, even though the agent never sees the answer. So the default demo hides the family
  and seed from *me* too, and reveals the planted truth next to the agent's answer with ✔/✘ when
  the incident closes. On the real stack the same idea is "sealed chaos": a random fault is
  injected and sealed in a file nobody reads until the incident is over.
- **Verification is code.** The alert's key metric must be ≤ 1.5× baseline and < 50 % of peak. On
  live systems it polls for up to 5 minutes. When the agent proposed a scale-out that was too small,
  the real lag kept climbing and verification said *not recovered* — the incident closed
  unresolved. The model proposes, the human approves, the system decides.
- **Memory.** Resolved incidents and runbooks are embedded locally (MiniLM) into pgvector; the
  recall step injects the top-3 as *hypotheses, not proof*. The benchmark is run with memory off so
  the number measures reasoning, not lookup.
- **Model-agnostic for real.** Ran on Gemini, Groq, Anthropic and a local 14B Ollama model with a
  two-line config change. Free tiers are counted in requests/day; the local model has no quota.

## What it can do

- Investigate an alert across metrics, logs, deploys, commits/diffs, flags, Kafka lag, cache
  health, hot partitions and dependency health.
- Diagnose one of six families — bad deploy, flag regression, consumer lag, cache eviction, hot
  partition, downstream outage — or say `unknown` and propose nothing.
- Propose exactly one fix from a fixed catalogue; wait for approval; execute; verify numerically.
- Run against a generated world (benchmarkable) or a real one (Prometheus / Kafka / Redis / GitHub).
- Be driven from a CLI, a web console, an HTTP API, an Alertmanager webhook, or any MCP client.
- Remember past incidents and recall similar ones.

## What it cannot do (say this before they ask)

- It does not discover *new kinds* of failure: the taxonomy is six families and the fix catalogue
  is seven actions. Outside that, the correct behaviour is `unknown` + no fix.
- One alert → one incident → one fix. No multi-alert correlation, no re-planning after a failed
  verify (that is the obvious next feature).
- The benchmark is synthetic and small (20). It catches regressions when prompts change; it is
  not a claim about real-world accuracy.
- The live demo system is tiny: one service, one consumer group, five injectable faults.
- Live adapters are thin: hot-key detection needs a datastore-specific exporter; deploy and flag
  history are file-backed plug points, not CI integrations.
