# Build plan

Each phase ends with a runnable thing. Tick when done (also mirror in PROGRESS.md).

## Phase 0 — Skeleton  ☑ (2026-11-09)
- `uv init`, py3.11, `pyproject.toml` with pinned deps, package layout, `pz` entry point, `.env.example`, `README.md` stub, `Makefile`.
- `pager_zero/config.py` (pydantic-settings), `pager_zero/llm.py` (`init_chat_model` factory).
- Runnable: `pz --help`, `pz doctor` (prints provider/model, checks key).

## Phase 1 — Synthetic world + adapters  ☑ (2026-11-09)
- `adapters/base.py` Protocols: MetricsReader, LogsReader, MessagingReader, CacheReader, DatastoreReader, SCMReader, DeployReader, FlagReader, TopologyReader + `Actuator` (7 actions). Bundle = `Adapters`.
- `synth/world.py` — World (services, deploys, commits, flags+history, metric series, logs, consumer groups, caches, tables, dependency health) + hidden `GroundTruth` with `matches(action, target)` (≥ / ≤ tolerance for sizes).
- `synth/builder.py` — fictional topology (checkout-api, order-service, pricing-service, offer-service, inventory-service, payment-gateway), baseline series, chatter logs, noise commits; `step`/`ramp` shapes.
- `synth/scenarios/` — bad-deploy (3 variants), flag-regression (3), consumer-lag (2), cache-eviction, hot-partition, downstream-outage (3 deps). Red herrings included.
- `adapters/synthetic.py` — one object serves every Protocol; correct action → world heals (`now` +3m, recovery series tail to baseline).
- `pz world list|show --scenario X --seed N [--truth]`; 33 tests.

## Phase 2 — MCP tool server  ☑ (2026-11-09)
- `mcp_server/server.py`: `build_server(adapters, actions_enabled, on_mutation, incident)` → FastMCP; `synthetic_server(scenario, seed, state, actions_enabled)` wires the synthetic world and persists it to `--state` (pickle, `synth/store.py`).
- 16 read tools: incident_context, service_info, query_metrics (stats + first_deviation_at + downsampled trace), search_logs (total + top_messages + sample), recent_deploys, recent_commits, get_diff, get_flag, flag_history, consumer_lag, lag_history, cache_stats, cache_history, hot_keys, partition_latency, dependency_health.
- 7 mutating tools exist **only** when `--actions`; annotated destructiveHint.
- `pz mcp serve [--scenario --seed --state --actions]`; README shows `claude mcp add …`.
- Tests: surface split, investigation trail, cross-process persistence/healing, stdio e2e via `langchain-mcp-adapters`.

## Phase 3 — LangGraph agent  ☑ (2026-11-09)
- `agent/graph.py`: triage (deterministic: incident_context + service_info) → recall (stub) → investigate ⟲ tools (step cap in state; on exhaustion a no-tools call forces a summary) → diagnose (`Diagnosis` structured) → plan_fix (`FixPlan` structured; `unknown`/low-confidence → unresolved, no plan) → approval (`interrupt`) → execute (actions session) → verify (deterministic: key metric last vs baseline/peak).
- `agent/runner.py`: `.pz/incidents/<id>/{meta.json,world.pkl,trace.jsonl}`, SQLite checkpointer, Rich rendering, `run_until_approval` / `resume` / `current_state`.
- CLI: `pz incident fire|approve|reject|list|show`, aliases `pz approve`, `pz reject`.
- Schemas are provider-neutral (no dict fields; `FixPlan.args` = list of key/value coerced by action signature).
- Verified live (gemini-3.5-flash-lite): bad-deploy seed 42 → rollback v1.59 (5 steps); cache-eviction seed 7 → set_cache_limit 16384 (8 steps); both resolved after `pz approve` from a new process.

## Phase 4 — Memory (pgvector)  ☑ (2026-11-09)
- `memory/store.py`: `MemoryStore` Protocol; `PgVectorStore` (table `memories`, VECTOR(384), HNSW cosine, degrades to [] when DB down), `InMemoryStore` (tests / no Docker), `NullStore`; `Embedder` = all-MiniLM-L6-v2 normalised; `incident_memory()` + `recall_query()`.
- `memory/runbooks.py`: 6 generic runbooks (one per family). `pz memory init|search|count`.
- `docker-compose.yml`: `pgvector/pgvector:pg16`, user/db `pz/pagerzero`.
- Runner: `recall` node queries top-`RECALL_K` (3) at incident start (shown in trace + console); `_remember()` after `resume` stores real diagnoses. `MEMORY_BACKEND=pgvector|memory|off`; per-incident `memory` flag (bench uses off by default).
- Verified live: consumer-lag#7 recalled "Consumer lag growing" runbook (sim 0.53) first; resolved via scale_consumers.

## Phase 5 — Benchmark  ◐ (code done 2026-11-09; first run in progress)
- `bench/scoring.py`: deterministic, no LLM judge — `root_cause_ok` (family), `culprit_ok` (diagnosis text names every culprit id), `fix_ok` (`GroundTruth.matches`), `verified` (metric recovered); tokens from `usage_metadata`.
- `bench/runner.py`: round-robin families, seeds `seed*1000+i`, quiet console, auto-approve, errors counted as failures, per-family + total table, `bench/results/<provider>__<model>__<stamp>.json` (committed).
- `pz bench --n 20 --seed 7 [--families] [--memory/--no-memory] [--pause]`.
- TODO: run on a stronger model too; put both tables in README; update resume numbers to measured.

## Phase 6 — FastAPI  ☑ (2026-11-09)
- `api/app.py`: POST/GET /incidents, /approve, /reject, /trace, /webhooks/alertmanager (needs `pz_scenario` label on synthetic), /healthz, /scenarios. Agent runs as asyncio background tasks; `pz serve`. Tests monkeypatch the runner.

## Phase 7 — Live adapters  ☑ (2026-11-09)
- `adapters/live.py`: PrometheusMetrics, LokiLogs, KafkaMessaging (kafka-python 3.x `list_group_offsets`/`describe_groups`), RedisCache, GitHubSCM, FileDeployLog, FileFlagStore, StaticTopology, PrometheusDatastore, **DryRunActuator**; `LiveAdapters.from_settings()`; `pz mcp serve` with `ADAPTER_BACKEND=live --alert-service …`.
- docker-compose `live` profile: prometheus :9090, redis **:6380** (host redis shadows 6379), redpanda :9092. Smoke-tested: Prometheus `up`, Redis maxmemory 64 MB, Kafka lag 6 (10 produced / 4 committed).
- Not done: demo-service + chaos script (optional, skipped).

## Phase 8 — Polish  ◐ (2026-11-09)
- ☑ README (pitch, transcript, architecture, bench table run 1 + honest misses, quick start, model table, CLI/API reference, live backend, limitations), ☑ GitHub Actions CI (ruff + pytest, MEMORY_BACKEND=memory), ☑ MIT LICENSE.
- ☐ Repo still **private** — flip to public when user says so. ☐ Demo GIF (asciinema) optional. ☐ Resume wording (proposal in PROGRESS). ☐ Post-fix bench re-run / stronger model (D-34).

## Phase 9 — Web approval UI  ☑ (2026-11-09, D-36)
- `api/static/index.html` served at `/ui` (`/` redirects). Left: fire form (scenario/seed) + incident list with status pills. Right: alert header, investigation trail (tool calls + click-to-expand results, recall hits), diagnosis card (family, confidence, evidence, ruled-out), proposed-fix card with **Approve & execute / Reject** when `awaiting_approval`, execution + verification cards. 2 s polling.
- Test: `/ui` 200 contains app; `/` → 307 `/ui`.

## Phase 10 — Blind mode  ☑ (2026-11-10, D-37)
- [x] `new_incident("random", None)` → blind; `public_meta()` hides scenario/seed until terminal; `truth_check()` reveals planted vs found
- [x] API: `POST /incidents` default `random`, `truth` field; CLI: `pz incident fire` default random, `incident show` prints ground truth
- [x] UI: 🎲 button primary, chosen-scenario under `<details>`, Ground-truth card with ✔/✘
- [x] 62 tests; verified live: blind cache-eviction #4441 → agent cache-eviction, all checks ✔

## Phase 11 — Demo stack with real faults  ☑ (merged to main 2026-11-10) (D-38/D-39, branch `feat/demo-stack`, 2026-11-10)
- [x] `demo/` self-contained compose: shop-api, payments-mock, worker×3, loadgen, exporter, Prometheus (+recording/alert rules), Alertmanager → `/webhooks/alertmanager` (no Grafana — Prometheus UI is enough for now)
- [x] `pz demo up|down|status|chaos|heal`; 5 faults (no hot-partition); `DemoActuator` real (`ACTUATOR=demo`)
- [x] live alert ingress + dedupe; `verify` polling for live; MCP subprocess env forwarding (P-26)
- [x] 78 tests; demo/README.md; main README section
- [x] end-to-end evidence (2026-11-10 13:45 IST, inc_45a8b32d, gemini-3.5-flash-lite): chaos bad-deploy → p99 24→495 → alert → live incident → `bad-deploy v1.5.0` / `rollback_deploy` in 40 s → approved → real rollback → p99 495→23.7 in 60 s → resolved
- [x] merged to main `68718b4`; branch deleted

## Deferred
- (none)
