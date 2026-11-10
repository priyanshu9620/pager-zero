# 05 — Codebase map, integrations, and how to extend it

## Layout (what lives where, and the one sentence that explains it)

```
pager-zero/
├── src/pager_zero/
│   ├── llm.py                 the ONLY provider-aware file: init_chat_model(provider, model)
│   ├── config.py              Settings from env/.env — every knob (LLM, backend, live URLs, limits)
│   ├── cli.py                 Typer CLI: doctor · world · mcp · incident · approve/reject · memory · bench · serve · demo
│   ├── errors.py              unwrap ExceptionGroups to the real cause; full tracebacks
│   │
│   ├── adapters/
│   │   ├── base.py            the CONTRACT: dataclasses (Series, LogLine, Deploy, …) + 9 reader Protocols + Actuator + ACTION_NAMES
│   │   ├── synthetic.py       one class serving a generated World through every Protocol; applies actions; heals
│   │   ├── live.py            Prometheus · Loki · Kafka · Redis · GitHub · file deploy/flag logs · static topology · dry-run actuator
│   │   └── __init__.py        get_adapters(): dispatch on ADAPTER_BACKEND
│   │
│   ├── synth/
│   │   ├── world.py           World, Service, CacheState, ConsumerGroupState, Alert, GroundTruth (hidden answer)
│   │   ├── builder.py         WorldBuilder: seeded baseline + helpers (series/step/ramp/log/deploy/flip_flag/alert/finish)
│   │   ├── scenarios/         one recipe per family: bad_deploy · flag_regression · consumer_lag · cache_eviction · hot_partition · downstream_outage
│   │   └── store.py           pickle the World so two processes share it
│   │
│   ├── mcp_server/server.py   FastMCP: 16 read tools (+7 mutating with --actions); LLM-shaped output digests; refresh/persist hooks
│   │
│   ├── agent/
│   │   ├── graph.py           LangGraph StateGraph: triage → recall → investigate ⟲ tools → diagnose → plan_fix → approval → execute → verify
│   │   ├── runner.py          incident lifecycle on disk (.pz/incidents/<id>/), MCP sessions, checkpointer, trace, blind/truth, live incidents
│   │   ├── prompts.py         investigate / diagnose / plan system prompts; action catalogue rendering
│   │   ├── schemas.py         Diagnosis · FixPlan · Verification (Pydantic) · ACTION_SIGNATURES
│   │   └── state.py           AgentState TypedDict
│   │
│   ├── memory/
│   │   ├── store.py           MemoryStore Protocol; PgVectorStore · InMemoryStore · NullStore; local MiniLM embedder
│   │   └── runbooks.py        6 seed runbooks
│   │
│   ├── bench/                 scoring.py (deterministic scorer) · runner.py (plan, run, summarise, write JSON)
│   ├── api/                   app.py (FastAPI: incidents, approve/reject, trace, Alertmanager webhook) · static/index.html (the UI)
│   └── demo/                  DemoStack (compose control, chaos, heal, seal) · actuator.py (the REAL actuator)
│
├── demo/                      the real stack: shop_api.py · worker.py · loadgen.py · payments_mock.py · exporter.py ·
│                              docker-compose.yml · prometheus.yml · rules.yml · alertmanager.yml · topology/flags/deploys templates
├── live/                      example topology/deploys/flags for pointing at your own infra
├── bench/results/             benchmark JSON outputs
├── tests/                     81 tests: synth · mcp server · agent graph (scripted LLM) · memory · scoring · api · live adapters · demo stack
├── docs/GUIDE.md              the user manual
├── INTERVIEW_PREP/            this folder
├── README.md · CLAUDE.md · Makefile · pyproject.toml (pinned deps) · .env.example · docker-compose.yml (pgvector) · .github/workflows/ci.yml
└── .pz/                       runtime state (gitignored): incidents/<id>/{meta.json, trace.jsonl, world.pkl, error.txt}, checkpoints.sqlite
```

## Dependency direction (the rule that keeps it clean)

```
cli / api / bench  ──►  agent  ──►  mcp_server  ──►  adapters/base (Protocols)
                          │                              ▲              ▲
                          └──► memory            synthetic + synth     live (+ demo actuator)
                          └──► llm.py
```
- The agent and the tool server import **only** the Protocols. They do not know which backend
  exists.
- Only `llm.py` knows about providers.
- `synth/` knows nothing about the agent; it is a pure generator + hidden answer.
- `demo/` knows about Docker; nothing else does.

## What it integrates with today

| category | integrations |
|---|---|
| LLM providers | Google Gemini, Groq, Anthropic, Ollama (anything LangChain's `init_chat_model` supports: OpenAI, Bedrock, …) |
| agent / tools | LangGraph (graph, interrupt, SQLite checkpointer), MCP (FastMCP server; `langchain-mcp-adapters` client), any MCP client (Claude Code, Cursor…) |
| observability | Prometheus (query_range, recording + alert rules), Alertmanager (webhook), Loki (optional) |
| messaging / cache / data | Kafka / Redpanda (admin API), Redis (INFO, CONFIG SET), Postgres (+ pgvector for memory) |
| SCM / deploy / flags | GitHub REST (commits, diffs); file-backed deploy log and flag store as plug points |
| runtime | Docker Compose (memory DB; the demo stack), FastAPI + one static page, Typer + Rich CLI, uv, GitHub Actions CI |

## How to add things (the questions interviewers like)

**A new LLM provider.** Install its LangChain package, set `LLM_PROVIDER`/`LLM_MODEL`. Zero code
unless it needs special kwargs — then `llm.py`, nowhere else. (If it can't do tool calling or
structured output, it can't run the agent — by design.)

**A new read tool** (say, `recent_alerts` or `trace_search`).
1. Add the data shape to `adapters/base.py` and a method on the relevant Protocol (or a new
   Protocol if it's a new source).
2. Implement it in `adapters/synthetic.py` (serve from the World — maybe extend the builder so
   scenarios can plant data) and in `adapters/live.py` (call the real API).
3. Register the tool in `mcp_server/server.py` with an LLM-shaped output.
4. Mention it in the investigate prompt if the model should know when to use it. Add tests.

**A new fix action** (say, `drain_node`).
1. Name it in `ACTION_NAMES` (base.py) and its typed arguments in `ACTION_SIGNATURES`
   (agent/schemas.py) — the catalogue in the plan prompt renders from that.
2. Add it to the `Actuator` Protocol, implement in the synthetic actuator (and decide which
   scenario it correctly fixes → `GroundTruth.matches`), the dry-run actuator, and the demo
   actuator if the demo stack can do it.
3. Register the mutating tool in the server (it's automatically behind `--actions`).

**A new incident family** (say, `disk-full`).
1. Write `synth/scenarios/disk_full.py`: pick targets, mutate series/logs, plant the cause, red
   herrings, recovery plan, alert, `GroundTruth`. Register it in `scenarios/__init__.py`.
2. Add the family name to the diagnose prompt's taxonomy (define it as the *trigger*).
3. If it needs a new fix action, see above. The benchmark picks it up automatically.
4. Optional: a demo-stack fault for it in `pager_zero/demo/__init__.py` (+ `EXPECTED_FIX`).

**A new live backend** (say, Datadog instead of Prometheus). Implement `MetricsReader` against the
Datadog API in a new module, return `Series`, and wire it in `LiveAdapters.from_settings()` behind
a setting. Nothing above the adapters changes.

**A new entry point** (say, PagerDuty webhooks instead of Alertmanager). Add a route in
`api/app.py` that maps the payload to `runner.new_live_incident({service, name, summary, labels})`
and spawns the run. Dedupe is already there.

**A re-plan loop after a failed verify** (the most requested next feature). In `agent/graph.py`,
add a conditional edge from `verify` back to `plan_fix` when not recovered and a retry counter is
below N, feeding the verification result into the plan prompt; the approval interrupt naturally
gates the second attempt too.

## Testing strategy (how I know it works)

- **Synthetic world tests** — every family generates, ground truth is consistent, correct action
  heals, wrong action doesn't.
- **MCP server tests** — call the tools in-process, assert output shapes.
- **Agent graph tests** — a *scripted* LLM (no network) drives the whole graph: approve path,
  reject path, step cap, checkpoint round-trip through real SQLite.
- **Live adapter tests** — HTTP mocked with `httpx.MockTransport`, fake Kafka/Redis clients, plus
  one real smoke against the demo stack when it's up.
- **Demo stack tests** — `docker compose` faked out; every fault changes something real; heal
  resets everything; the actuator's actions are the inverses.
- **API tests** — runner monkeypatched; lifecycle, blind hide/reveal, webhook ingress + dedupe.
- **Benchmark** — the integration test with numbers.
- CI runs lint + the 81 tests on every push with memory in-process (no Docker, no keys).
