# Pager-Zero — setup & user guide

Everything you need to install, run, understand and troubleshoot Pager-Zero on a fresh laptop.
The README is the pitch; this is the manual. If something here is wrong, the code wins — fix the doc.

---

## 1. What this is (60 seconds)

Pager-Zero is an on-call agent. Give it an alert and it:

1. **investigates** — calls read-only tools (metrics, logs, deploys, flags, Kafka lag, cache stats,
   dependency health) the way an engineer opens dashboards;
2. **diagnoses** — names a root-cause family, the culprit, the evidence, and what it ruled out;
3. **proposes one fix** — rollback, flag revert, scale consumers, raise a cache limit, enable a
   fallback — and **stops**;
4. waits for **you** to approve or reject;
5. on approval **executes** the fix and **verifies** — code (not the model) re-reads the key metric
   and declares recovered / not recovered.

It can run against two kinds of world, chosen by one config value (`ADAPTER_BACKEND`):

| backend | what the tools read | what "execute" does | when to use |
|---|---|---|---|
| `synthetic` (default) | a generated world with a hidden planted cause | mutates the generated world | learning, demos, the benchmark |
| `live` | real Prometheus / Kafka / Redis / deploy log / flag file | dry-run by default; `ACTUATOR=demo` really changes the demo stack | the demo stack, or your own infra |

The agent code is identical in both. Only the adapters behind the tools change.

---

## 2. Prerequisites

| need | why | install |
|---|---|---|
| **uv** | Python env + deps (it downloads Python 3.11 itself) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Docker Desktop**, running | pgvector memory (optional) and the demo stack | https://www.docker.com/products/docker-desktop |
| **one LLM** | the agent's brain | see §3 |
| ~15 GB disk | Docker images (+9 GB if you use Ollama) | — |

macOS and Linux are tested. Windows: use WSL2.

---

## 3. Choosing an LLM

Pager-Zero is provider-agnostic (`init_chat_model`): set two lines in `.env`.

| option | `.env` | cost / limits | notes |
|---|---|---|---|
| **Google Gemini** (recommended free) | `LLM_PROVIDER=google_genai`<br>`LLM_MODEL=gemini-3.5-flash-lite`<br>`GOOGLE_API_KEY=…` | free; **~500 requests/day per model** (≈ 30–40 incidents) | key: https://aistudio.google.com → *Get API key*. Fast (1–2 s/turn), accurate on the bench. |
| **Ollama** (local, unlimited) | `LLM_PROVIDER=ollama`<br>`LLM_MODEL=qwen2.5:14b` | free, no quota, no network | `brew install ollama && ollama pull qwen2.5:14b` (9 GB; needs ~16 GB RAM). ~2 min/incident, somewhat weaker. |
| Anthropic | `LLM_PROVIDER=anthropic` `LLM_MODEL=claude-opus-5-5` `ANTHROPIC_API_KEY=…` | paid | strongest |
| Groq | `LLM_PROVIDER=groq` `LLM_MODEL=openai/gpt-oss-120b` `GROQ_API_KEY=…` | free tier too small (8k tok/min, 200k tok/day) | demo only |

One investigation ≈ 7–15 LLM requests. If you hit a quota you'll see an incident end in `error`
with a 429 in `.pz/incidents/<id>/error.txt`; switch provider or wait (Gemini resets ~13:30 IST).

---

## 4. Install (5 minutes)

```bash
git clone https://github.com/priyanshu9620/pager-zero && cd pager-zero
uv sync                              # creates .venv, installs pinned deps
cp .env.example .env                 # edit: pick ONE provider block (§3)
uv run pz doctor --ping              # prints effective config; must end with "model says: pong"
make test                            # 81 tests, no key needed — proves the install
```

Optional — memory (RAG over past incidents + runbooks, pgvector):

```bash
docker compose up -d                 # Postgres + pgvector on :5432
uv run pz memory init                # loads 6 seed runbooks
```
Without it, `recall` is simply empty; nothing else changes. `MEMORY_BACKEND=off` silences it.

---

## 5. Synthetic mode — your first incident

### CLI
```bash
uv run pz incident fire              # BLIND: a family + seed are chosen and hidden from you
# … triage → recall → investigate (tool calls stream) → diagnose → plan_fix → ⏸ approval required
uv run pz approve inc_xxxxxxxx       # execute + verify → resolved / unresolved
uv run pz incident show inc_xxxxxxxx # diagnosis, plan, verification, and the REVEALED ground truth
uv run pz reject inc_xxxxxxxx --reason "not convinced"
uv run pz incident list
```
Pick the failure yourself: `pz incident fire -s cache-eviction --seed 7` (families: `bad-deploy`,
`flag-regression`, `consumer-lag`, `cache-eviction`, `hot-partition`, `downstream-outage`).
`pz world show -s bad-deploy --seed 42 --truth` prints a generated world including its hidden answer.

### Web console
```bash
uv run pz serve                      # http://127.0.0.1:8000/ui   (API docs at /docs)
```

---

## 6. The web UI, panel by panel

```
┌─ header ───────────────────────────────────────────── model · backend · memory ─┐
├─ left ───────────────┬─ right (selected incident) ─────────────────────────────┤
│ 🎲 Fire a random     │ ALERT CARD   name · service · summary · fired · status   │
│    incident          │   pipeline:  triage → investigate → diagnose → plan fix │
│ ▸ choose the failure │              → approval → execute → verify              │
│   yourself           │ INVESTIGATION  numbered tool calls, each with a one-line│
│                      │   summary + sparkline of the metric it fetched; "raw ▾" │
│ INCIDENTS            │ DIAGNOSIS    family · confidence bar · root cause ·      │
│  🎲 blind incident   │              evidence ▸ · ruled out                     │
│  ⚡ HighLatencyP99   │ PROPOSED FIX action(args) · why · blast radius ·         │
│  cache-eviction #7   │              [Approve & execute] [Reject] + note         │
│  …                   │ EXECUTION    what was actually done                      │
│                      │ VERIFICATION ✔ Recovered / ✘ Not recovered + numbers     │
│                      │ GROUND TRUTH planted vs agent, ✔/✘ (synthetic or sealed) │
└──────────────────────┴─────────────────────────────────────────────────────────┘
```

- **Header** — which model/backend/memory this server is running. `backend live` means the
  tools are reading real systems.
- **🎲 Fire a random incident** — synthetic, *blind*: the server picks family + seed and hides
  both from you and the agent until the incident is over. "choose the failure yourself" lets you
  pick family + seed explicitly (useful for reproducing a case: same seed = same world).
- **Incident list** — newest first. Prefixes: 🎲 blind synthetic · plain = chosen synthetic ·
  ⚡ live (came in from Alertmanager; label is the alert name + service). The pill is the status:
  `investigating` → `awaiting_approval` → `executing` → `resolved` | `unresolved` | `rejected` | `error`.
- **Pipeline tracker** — green done, pulsing teal in progress, yellow = waiting for you, red = rejected/failed.
- **Investigation** — one row per tool call, in order. The summary is derived from the tool's
  output (e.g. "**23.7** → **494.8** +1990% · deviated 13:44"); red sparkline = that series moved a
  lot. Click `raw ▾` to see exactly what the model saw. "✎" rows are the model thinking aloud.
- **Diagnosis** — `family` is one of the six (or `unknown` if the agent won't guess — then no fix
  is proposed and the incident ends `unresolved`, by design).
- **Proposed fix** — exactly one action from the catalogue, with arguments. Approve runs it;
  Reject ends the incident. Your note goes into the audit trail (`trace.jsonl`).
- **Verification** — code, not the model: the alert's key metric (p99 for latency alerts, lag
  for lag alerts, error rate for error alerts) must be ≤ 1.5× its baseline **and** < 50 % of its
  peak. Live backends poll for up to `VERIFY_TIMEOUT_S` seconds.
- **Ground truth** — appears only when the incident is over. Synthetic: the planted family,
  cause, culprit and exact expected fix, with ✔/✘ for root cause / culprit named / fix matches /
  recovery. Live + sealed chaos (§7): "What was actually broken" with ✔/✘ for family / right fix
  action / recovery.

Everything the UI shows is also in the API (`GET /incidents/{id}`, `/trace`) and on disk under
`.pz/incidents/<id>/` (`meta.json`, `trace.jsonl`, `world.pkl`, `error.txt` on failure).

---

## 7. Real-system mode — the demo stack

A small **real** system you break on purpose: FastAPI `shop-api` on Postgres + Redis, a Kafka
consumer group (`order-worker` ×3) on Redpanda, a load generator, a `payments-mock` dependency,
Prometheus (with recording + alert rules) and Alertmanager. Details: [`../demo/README.md`](../demo/README.md).

```bash
uv run pz demo up                    # builds images, starts 9 containers (first time ~2 min)
make demo-serve                      # = ADAPTER_BACKEND=live ACTUATOR=demo … uv run pz serve --host 0.0.0.0
                                     # open http://127.0.0.1:8000/ui — header shows "backend live"
uv run pz demo chaos                 # inject a SEALED random fault (you are not told which)
#   or name one: uv run pz demo chaos bad-deploy | flag-regression | cache-eviction | consumer-lag | downstream-outage
# ~60–120 s: Prometheus alert → Alertmanager → webhook → ⚡ incident appears in the UI by itself
# read the investigation → Approve → the fix really runs → verify watches the real metric
uv run pz demo reveal                # which fault was sealed (also shown on the incident card when it closes)
uv run pz demo heal                  # undo every fault; clears the seal
uv run pz demo status                # containers + current knobs
uv run pz demo down                  # stop (add -v to delete data)
```

What each fault really does and what the right fix is: see the table in `demo/README.md`.
Useful views while it runs: Prometheus http://localhost:9090 (alerts at `/alerts`, graph
`http_p99_ms`), Alertmanager http://localhost:9093, the app http://localhost:8010/offers/42.

Expect imperfect runs — that is the point. Example from development: sealed fault
`consumer-lag`; the agent diagnosed it correctly and proposed `scale_consumers(members=2)`, which
really ran but was too small; the real lag kept climbing; verification said **not recovered**. The
card showed ✔ family · ✔ right action · ✘ recovery.

### Pointing it at your own infrastructure
Same live adapters, your endpoints. Set `ADAPTER_BACKEND=live`, `PROMETHEUS_URL`,
`KAFKA_BOOTSTRAP`, `REDIS_URL`, optional `LOKI_URL` / `GITHUB_TOKEN`, write
`live/topology.json` (copy `live/topology.example.json`), and point your Alertmanager at
`POST http://<host>:8000/webhooks/alertmanager` (alerts need `service` and `alertname` labels).
Metric names the agent expects: `http_p99_ms`, `http_error_rate`, `request_rate`, `db_qps`,
`cache_hit_rate`, `dependency_error_rate{service,dependency}`, `kafka_consumergroup_lag_sum{consumergroup}`,
`redis_*` — produce them with recording rules like `demo/rules.yml`. The actuator stays **dry-run**
unless you deliberately wire a real one.

---

## 8. Benchmark

```bash
uv run pz bench --n 20 --seed 7 --no-memory          # ~25–30 min on gemini-3.5-flash-lite
uv run pz bench --n 10 --families bad-deploy,consumer-lag
```
Scores against the hidden ground truth with no LLM judge: root-cause family, culprit named, fix
matches, verified recovery. Results land in `bench/results/*.json`; the README table is
hand-copied from the printed summary. Budget: ~250 LLM requests per 20 incidents ≈ half a free
Gemini day.

---

## 9. Every command

```
pz doctor [--ping]                         effective config, key check, model ping
pz world list | show -s <family> --seed N [--truth]
pz incident fire [-s <family>|random] [--seed N]     run to the approval point
pz approve <id> [--note …] | pz reject <id> [--reason …]
pz incident list | show <id>
pz memory init | search "<text>" | count
pz bench --n 20 --seed 7 [--families a,b] [--memory] [--pause s]
pz serve [--host 0.0.0.0] [--port 8000]    API + UI
pz mcp serve [-s … --seed N] [--state f] [--actions]      MCP server over stdio (any MCP client)
pz demo up [--no-reset] | down [-v] | status | chaos [fault|random] | reveal | heal

make test | lint | fmt | demo-up | demo-serve | demo-down
```

Config reference: `.env.example` (every key, commented). Dynamic knobs: `MAX_STEPS` (tool-call
cap, 12), `VERIFY_TIMEOUT_S` (live verify polling), `MEMORY_BACKEND` (`pgvector|memory|off`),
`ACTUATOR` (`dry-run|demo`).

---

## 10. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `pz doctor --ping` → 429 / `RESOURCE_EXHAUSTED` | free quota used up | wait for reset, switch provider, or Ollama |
| incident ends `error` | read `.pz/incidents/<id>/error.txt` (full traceback) | usually quota (429) or a downstream unreachable |
| UI shows an incident but the pane is empty | stale page | hard refresh (Cmd+Shift+R); the server sends `no-store` now |
| ⚡ incidents never appear after `pz demo chaos` | Alertmanager can't reach the server | you must use `make demo-serve` (binds 0.0.0.0); check http://localhost:9093 shows the alert |
| alert fired but a second chaos produces no new incident | Alertmanager won't re-notify the same firing alert (`repeat_interval` 2 h) | `pz demo heal`, wait until http://localhost:9090/alerts is green, then chaos again |
| live tools return `points: 0` | Prometheus has no history yet / rules just loaded | wait a minute; check http://localhost:9090/targets are UP |
| `redis maxmemory 0` or wrong cache numbers | a local Redis on 6379 is shadowing | demo uses 6380 — ensure `REDIS_URL=redis://localhost:6380/0` (make demo-serve sets it) |
| verify says not recovered but Prometheus shows recovery | the metric window was frozen (fixed) / `VERIFY_TIMEOUT_S=0` on live | set `VERIFY_TIMEOUT_S=300` (make demo-serve does) |
| memory errors / `pz memory init` fails | Postgres not up | `docker compose up -d`; or `MEMORY_BACKEND=off` |
| `uv sync` fails on Python | uv couldn't install 3.11 (e.g. `~/.local` not writable) | `brew install python@3.11 && uv python pin 3.11` |
| ports busy | 5432/5433, 6380, 8000, 8010, 9090, 9092, 9093 | stop the other process or edit `demo/docker-compose.yml` |

---

## 11. Where things live

```
src/pager_zero/
  llm.py            the ONLY provider-aware file (init_chat_model)
  config.py         Settings — every env var
  adapters/base.py  the Protocols every backend implements; ACTION_NAMES
  adapters/synthetic.py · adapters/live.py
  synth/            generated worlds: builder, 6 scenario recipes, hidden GroundTruth
  mcp_server/       16 read tools + 7 mutating tools (only with --actions)
  agent/            LangGraph graph (triage→recall→investigate⟲→diagnose→plan→approval→execute→verify), runner, prompts, schemas
  memory/           pgvector / in-process / off stores, runbooks
  bench/            scoring + runner
  api/              FastAPI + static/index.html (the UI, one file)
  demo/             demo-stack control, chaos, real actuator
demo/               the real stack: services, compose, prometheus rules, alertmanager
tests/              81 tests (make test)
.pz/                runtime state: incidents/, checkpoints.sqlite   (gitignored)
docs/GUIDE.md       this file
```

## Tracing with LangSmith (optional)

Every agent run can be recorded as a trace tree: each LLM call with its exact prompt and response, every
tool call with inputs/outputs, latency, tokens and cost per step. LangGraph picks this up from the
environment — no code path changes:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...        # smith.langchain.com → Settings → API keys (free tier: 5k traces/month)
LANGSMITH_PROJECT=pager-zero
```

`pz doctor` shows `tracing  LangSmith ✔ project pager-zero` when it is on. Then open
https://smith.langchain.com → project *pager-zero*: one root run per incident (`pz incident fire`,
`pz approve`, the webhook path, `pz bench`), expandable to the node → LLM → tool level.

What it is for here:
- debugging a wrong decision: open the failed incident's trace and read the prompt that produced it;
- cost/latency per model: the Gemini vs Groq vs Ollama comparison with real token counts;
- regression: save bench scenarios as a LangSmith dataset and re-run them after a prompt change.

Traces leave the laptop (synthetic incidents only — nothing sensitive). Leave the three variables unset to
run fully offline; nothing else changes.
