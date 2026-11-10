# Pager-Zero

**An LLM on-call agent that diagnoses a production incident and prepares the fix — then waits for a human to approve.**

Hand it an alert. It investigates the way an engineer would (metrics, logs, recent deploys, flag
changes, consumer lag, cache health, hot partitions, dependency health), produces a root-cause
diagnosis with cited evidence, proposes one concrete remediation (rollback, revert flag, scale
consumers, raise cache limit, rate-limit a key, enable a fallback), and **pauses**. You approve from
the CLI or the API; only then does it execute — and it verifies the metric actually recovered.

```
$ pz incident fire -s cache-eviction --seed 7
[triage]      HighLatencyP99 on offer-service — p99 > 800ms for 2m
[recall]      1 memory hit: runbook "Cache hit-rate collapse" (sim 0.52)
[investigate] → query_metrics(http_p99_ms, offer-service)        +400% from 22:37
              → recent_deploys(offer-service)                    0 items    ← ruled out
              → flag_history(offers.cache.ttl_seconds)           unchanged  ← ruled out
              → cache_history(offer-cache, hit_rate)             0.98 → 0.39
              → cache_history(offer-cache, evictions)            0.5 → 18,000/s
              → search_logs(offer-service, warn)                 205× "maxmemory 8192MB reached"
[diagnose]    cache-eviction (conf 1.00): offer-cache hit its 8192MB maxmemory after the catalog
              refresh doubled key count; eviction storm dropped hit-rate to ~40% and pushed reads to the DB
[plan_fix]    set_cache_limit(offer-cache, 16384)  risk=low
⏸  approval required:  pz approve inc_526d0985

$ pz approve inc_526d0985
[execute]     offer-cache: maxmemory 8192 → 16384 MB — metrics recovering
[verify]      http_p99_ms peak 591 → now 120 (baseline 118)  ✔ recovered
inc_526d0985 → resolved
```

Everything above ran on a **free** `gemini-3.5-flash-lite` key. Swap the model with two lines in `.env`.

**Not convinced it isn't rigged?** Fire a *blind* incident: `pz incident fire` (no `-s`) or the 🎲
button in the UI picks the failure family and seed server-side and hides both from you *and* the
agent until the incident is over — then shows the planted truth next to what the agent concluded,
with ✔/✘ on root cause, culprit and fix. The agent only ever sees the alert and the tools.

---

## Why

The first 10–30 minutes of most incidents are mechanical: open dashboards, line the spike up
with the last deploy, grep logs, check lag. That loop is tool-driven and repeatable. Pager-Zero
does it while you're still waking up — and stops short of touching anything without you.

Design principles:

- **Prepare, don't act.** Mutating tools are not even *registered* in the investigation session; a
  separate actions-enabled session is opened only after a human resolves the approval interrupt.
- **Bounded.** Hard cap on tool calls per incident (`MAX_STEPS`, default 12). On exhaustion the agent
  must summarise what it has.
- **Verifiable.** Verification is code, not the model: it re-queries the alert's key metric and
  compares it numerically to baseline and peak. A wrong fix shows up as "not recovered".
- **Model-agnostic.** One `init_chat_model` factory; no provider-specific features anywhere.
- **Benchmarked.** A synthetic incident generator with hidden ground truth makes accuracy a number,
  not a feeling.

## Architecture

```
  pz CLI  /  FastAPI (POST /incidents, /approve, Alertmanager webhook)
      │
      ▼
  LangGraph agent   triage → recall → investigate ⟲(≤12) → diagnose → plan_fix → ⏸ approval → execute → verify
      │ read-only MCP session                                     │ actions MCP session   │ pgvector
      ▼                                                           ▼                       ▼
  MCP tool server (FastMCP)                                                        Postgres + pgvector
    16 read tools: incident_context · service_info · query_metrics · search_logs       (incidents, runbooks,
    recent_deploys · recent_commits · get_diff · get_flag · flag_history               MiniLM embeddings)
    consumer_lag · lag_history · cache_stats · cache_history · hot_keys
    partition_latency · dependency_health
    7 mutating tools (only with --actions): rollback_deploy · set_flag · scale_consumers
    restart_consumer_group · set_cache_limit · rate_limit_key · enable_fallback
      │ Protocols (adapters/base.py)
      ▼
  adapters:  synthetic (generated World)  │  live: Prometheus · Loki · Kafka · Redis · GitHub · file-backed deploys/flags
```

- **Synthetic world** (`synth/`): six incident families — `bad-deploy`, `flag-regression`,
  `consumer-lag`, `cache-eviction`, `hot-partition`, `downstream-outage` — each with variants and
  red herrings (an unrelated deploy, an old flag flip, a harmless sibling commit). Deterministic by
  `(scenario, seed)`. The correct fix *heals* the world so verification is real.
- **Adapters** (`adapters/`): Python `Protocol`s; the synthetic backend serves a `World`, the live
  backend wraps real APIs. Chosen with `ADAPTER_BACKEND=synthetic|live`.
- **MCP server** (`mcp_server/`): any MCP client (Claude Code, Cursor, your agent) can drive the tools.
  Outputs are pre-digested for LLMs (series → stats + first-deviation time + 16-point trace; logs →
  counts + top messages + sample).
- **Agent** (`agent/`): LangGraph `StateGraph`, SQLite checkpointer, `interrupt()` for approval —
  `pz approve` can run hours later in another process. Structured outputs via Pydantic.
- **Memory** (`memory/`): resolved incidents + runbooks, embedded locally (no API key), recalled as
  hypotheses — never as proof.
- **Bench** (`bench/`): N incidents, auto-approve, deterministic scoring, JSON results.

## Benchmark

20 synthetic incidents (round-robin over the 6 families, seed 7, memory off), auto-approved, scored
against the hidden ground truth. Scoring is deterministic — no LLM judge:

| metric | meaning |
|---|---|
| root cause | diagnosed family == planted family |
| culprit named | diagnosis text names the exact version / commit / flag / group / cache / key / dependency |
| fix | proposed action **and** target match ground truth (≥ for sizes, ≤ for rate limits) |
| verified | after executing, the alert's metric actually recovered |

**`gemini-3.5-flash-lite` (free tier) — run 1, 2026-11-09** · [`bench/results/`](bench/results/)

| family | n | root cause | culprit named | fix | verified | avg steps | avg s |
|---|---:|---:|---:|---:|---:|---:|---:|
| bad-deploy | 4 | 4 (100%) | 4 (100%) | 4 (100%) | 4 (100%) | 5.2 | 28.6 |
| cache-eviction | 4 | 4 (100%) | 4 (100%) | 4 (100%) | 4 (100%) | 8.5 | 39.0 |
| consumer-lag | 3 | 3 (100%) | 2 (67%) | 3 (100%) | 3 (100%) | 8.3 | 31.1 |
| downstream-outage | 3 | 3 (100%) | 3 (100%) | 3 (100%) | 3 (100%) | 9.3 | 57.8 |
| flag-regression | 3 | 2 (67%) | 3 (100%) | 2 (67%) | 2 (67%) | 5.3 | 15.4 |
| hot-partition | 3 | 3 (100%) | 3 (100%) | 3 (100%) | 3 (100%) | 10.0 | 32.8 |
| **total** | **20** | **19 (95%)** | **19 (95%)** | **19 (95%)** | **19 (95%)** | **7.7** | **34.1** |

~33k input / 0.4k output tokens per incident. The three misses: one diagnosis didn't name the
consumer group (fair); one labelled a flag-induced cache-miss storm as `cache-eviction` instead of
`flag-regression` (cause and fix were right; the prompt now defines family as the *trigger*); one
proposed `set_flag(value="auto")` because the action catalogue leaked an internal type tag — a bug
in this repo, since fixed. Post-fix re-runs and a stronger-model row will be added as quota allows.

Reproduce: `uv run pz bench --n 20 --seed 7 --no-memory`. A 20-incident run costs ~0.7M input
tokens — roughly one day of a free Gemini quota.

## Quick start

```bash
git clone https://github.com/priyanshu9620/pager-zero && cd pager-zero
uv sync                       # Python 3.11+, uv
cp .env.example .env          # set ONE provider key (Gemini free tier works)
uv run pz doctor --ping       # → model says: pong

docker compose up -d          # Postgres + pgvector (optional: memory degrades to "off" without it)
uv run pz memory init         # load runbooks

uv run pz incident fire -s bad-deploy --seed 42
uv run pz approve <incident id>
```

### Choosing a model

| provider | `LLM_PROVIDER` | `LLM_MODEL` | notes |
|---|---|---|---|
| Google AI Studio | `google_genai` | `gemini-3.5-flash-lite` | free; ~1.4 s per tool-calling turn; benchmarked above |
| Google AI Studio | `google_genai` | `gemini-3.5-flash` | free; stronger, ~5–11 s/turn |
| Anthropic | `anthropic` | `claude-opus-5-5` | paid |
| Groq | `groq` | `openai/gpt-oss-120b` | free tier's 8k tokens/min is too small for a 12-step agent |
| Ollama | `ollama` | `qwen2.5:14b` | local, **no key, no quota**; ~2 min per incident on an M4 Pro; solved a blind `downstream-outage` 3/3 but missed a live `bad-deploy` (declined to guess) |

Free cloud tiers are counted in **requests/day** (Gemini: 500 per model) or **tokens/day** (Groq:
200k); one investigation is 7–15 requests, so a heavy day of testing exhausts them by afternoon.
Ollama is the way out when that happens — same agent, same tools, two lines of config.

Only tool calling, plain text and Pydantic structured output are used, so anything LangChain's
`init_chat_model` supports should work.

## CLI

```
pz doctor [--ping]                         effective config, key check, model ping
pz world list | show -s <scenario> --seed N [--truth]
pz mcp serve [-s … --seed N] [--state f] [--actions]     MCP server over stdio
pz incident fire [-s <scenario> --seed N]  run the agent to the approval point (default: blind random)
pz approve <id> | pz reject <id>           resume: execute + verify, or stop
pz incident list | show <id>
pz memory init | search "<text>" | count
pz bench --n 20 --seed 7 [--families a,b] [--memory] [--pause s]
pz serve [--host 0.0.0.0 --port 8000]      FastAPI (docs at /docs)
pz demo up | down | status | chaos <fault> | heal     the real demo stack (demo/README.md)
```

## Web approval console

```bash
uv run pz serve            # then open http://127.0.0.1:8000/ui
```

Fire a random (blind) incident, watch the tool calls stream in with inline sparklines, read the
diagnosis and the proposed fix, click **Approve & execute** or **Reject**, then compare the revealed
ground truth with the agent's answer. One static page served by the API — no build step.

## HTTP API

```
POST /incidents                 {"scenario": "random"} or {"scenario": "bad-deploy", "seed": 42}  → 202
GET  /incidents/{id}            status, diagnosis, plan, verification; `truth` (planted vs found) once over
GET  /incidents/{id}/trace      audit trail
POST /incidents/{id}/approve    {"note": "..."}  → 202
POST /incidents/{id}/reject
POST /webhooks/alertmanager     Alertmanager payload (synthetic backend needs a `pz_scenario` label)
GET  /healthz · GET /scenarios
```

## Drive the tools yourself (any MCP client)

```bash
claude mcp add pager-zero -- uv --directory $PWD run pz mcp serve --scenario hot-partition --seed 3
```
Then: *"Call incident_context and find the root cause."* Mutating tools appear only with `--actions`.

## Demo stack: break a real system

The synthetic world is a benchmark. To watch the agent work on something that is actually
broken, [`demo/`](demo/README.md) ships a small **real** system — a FastAPI service on Postgres +
Redis, a Kafka consumer group on Redpanda, a load generator, Prometheus and Alertmanager — and a
chaos command that does real damage:

```bash
uv run pz demo up                     # 9 containers
make demo-serve                       # pz serve against the live adapters, real actuator
uv run pz demo chaos                  # random + sealed — or name one: bad-deploy | flag-regression | cache-eviction | consumer-lag | downstream-outage
```

~1–2 minutes later Alertmanager pages Pager-Zero, a ⚡ live incident appears in the UI, the agent
investigates through Prometheus / Kafka / Redis / the deploy log, proposes a fix, and on approval
**really** rolls the container back (or rescales the group, or `CONFIG SET`s Redis) — and
verification watches the real p99 come down. Nothing in the agent knows what you broke — and
with a sealed random fault, neither do you until the incident closes and the card reveals it.

Measured run (2026-11-10, `gemini-3.5-flash-lite`): deploy injected at T+0 → p99 24 → 495 ms by
T+20 s → alert fired T+75 s → incident opened T+85 s → diagnosis `bad-deploy v1.5.0`,
`rollback_deploy(shop-api, v1.4.0)` at T+125 s (7 tool calls) → approved → container rolled back →
`http_p99_ms peak 495.0 → now 23.7 (baseline 23.7)` 60 s later → **resolved**.

## Live backend

`ADAPTER_BACKEND=live` swaps the synthetic world for real sources through the same Protocols:

| reader | implementation | config |
|---|---|---|
| metrics | Prometheus HTTP API | `PROMETHEUS_URL` |
| logs | Loki HTTP API (optional) | `LOKI_URL` |
| messaging | Kafka admin offsets (`kafka-python`) + Prometheus for history | `KAFKA_BOOTSTRAP` |
| cache | Redis `INFO` + Prometheus for history | `REDIS_URL` |
| scm | GitHub REST (commits, diffs) | `GITHUB_TOKEN`, repo per service in topology |
| deploys / flags | JSON files your CI / flag service append to | `live/deploys.json`, `live/flags.json` |
| topology | static JSON | `live/topology.json` (see `live/*.example.json`) |
| actuator | **dry-run by default** — logs the action, changes nothing | `ACTUATOR=demo` for the demo stack's real actuator |
| verify | polls the key metric until it recovers or `VERIFY_TIMEOUT_S` elapses | `VERIFY_TIMEOUT_S=300` (0 = check once) |

```bash
docker compose --profile live up -d      # prometheus :9090, redis :6380, redpanda :9092
cp live/topology.example.json live/topology.json   # and deploys/flags
ADAPTER_BACKEND=live REDIS_URL=redis://localhost:6380/0 \
  uv run pz mcp serve --alert-service checkout-api --alert-name HighLatencyP99
```

## Development

```bash
make test        # 78 tests, no API key needed (MEMORY_BACKEND=memory)
make lint
PZ_TEST_PG=1 uv run pytest tests/test_memory.py   # with docker compose up
```

## Limitations (honest list)

- The benchmark is synthetic and small (20). It catches regressions when prompts change; it is not
  a claim about real-world incidents.
- Live adapters are thin: hot-key detection needs a datastore-specific exporter (returns empty);
  deploy and flag history are file-backed plug points, not integrations with a specific CI or flag
  service.
- One alert → one incident → one fix. No multi-alert correlation yet.
- The demo stack is tiny (one service, one worker) and its faults are the five the actuator can
  undo; it proves the agent works on real telemetry, not that it generalises to your production.

## License

MIT
