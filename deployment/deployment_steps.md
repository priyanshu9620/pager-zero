# Pager-Zero — how it runs, where it would be deployed, and how to demo it

Plain-language guide. Read this first when coming back to the repo after a while.
Full manual: [docs/GUIDE.md](../docs/GUIDE.md) · interview notes: [INTERVIEW_PREP/](../INTERVIEW_PREP/) ·
fresh laptop: [handoff/README.md](../handoff/README.md)

---

## 1. What runs where

Pager-Zero is **one Python process**, not a separate frontend and backend.

| Piece | What it is | Where it runs |
|---|---|---|
| **The app** (`uv run pz serve`) | FastAPI on port 8000. The same process serves the UI (one static HTML file at `/ui`), the API (`/docs`), the Alertmanager webhook, and runs the LangGraph agent | laptop today; a single small VM if ever hosted |
| **LLM** | one provider key: Gemini / Groq / Anthropic / OpenAI, or Ollama locally (`LLM_PROVIDER`, `LLM_MODEL` in `.env`) | the provider's cloud (or Ollama on the laptop) |
| **Memory** (optional) | Postgres + pgvector for incident recall (`MEMORY_BACKEND=pgvector`); `memory` or `off` need nothing | `docker-compose.yml` in the repo root |
| **Demo stack** (optional) | 9 containers: shop-api, Postgres, Redis, Redpanda, 3 × order-worker, payments-mock, Prometheus, Alertmanager | `demo/docker-compose.yml`, laptop only |

Two worlds, chosen by `ADAPTER_BACKEND`:
- `synthetic` (default): a generated system with a hidden planted fault. No Docker needed.
- `live`: the agent's tools read real Prometheus / Kafka / Redis / deploy log / flag file. With `ACTUATOR=demo` the approved fix really changes the demo containers.

The agent code is identical in both; only the adapters behind the tools change.

**Deployment status:** not deployed anywhere. It has only ever run on the laptop. Nothing here is on AWS.

---

## 2. Mode A — synthetic demo (2 minutes, no Docker)

Needs `uv`, a working LLM key in `.env`, and nothing else.

```bash
cd ~/Desktop/pager-zero
uv run pz doctor --ping            # confirms config + the key answers (do this an hour before)
uv run pz serve                    # http://127.0.0.1:8000/ui   (API docs at /docs)
```

In the UI: fire an incident, watch the stream (triage → recall → investigate with tool calls → diagnose → plan fix → **pause for approval**), approve, watch execute + verify.

Same thing from the CLI:

```bash
uv run pz incident fire                         # blind: family + seed hidden from you
uv run pz incident fire -s consumer-lag --seed 7   # or choose the fault
uv run pz approve inc_xxxxxxxx                  # execute + verify
uv run pz incident show inc_xxxxxxxx            # diagnosis, plan, verification, revealed ground truth
```

Families: `bad-deploy`, `flag-regression`, `consumer-lag`, `cache-eviction`, `hot-partition`, `downstream-outage`.

---

## 3. Mode B — real-system demo (5 minutes, the impressive one)

Needs Docker Desktop. Start the stack **before** the call (first build ~2 min).

```bash
cd ~/Desktop/pager-zero
uv run pz demo up                  # 9 containers
make demo-serve                    # ADAPTER_BACKEND=live ACTUATOR=demo, binds 0.0.0.0 so Alertmanager can reach it
                                   # open http://127.0.0.1:8000/ui — header must say "backend live"
```

During the call:

```bash
uv run pz demo chaos               # inject a SEALED random fault (or name one: bad-deploy | flag-regression | cache-eviction | consumer-lag | downstream-outage)
# 60–120 s later: Prometheus alert → Alertmanager → webhook → ⚡ incident appears in the UI by itself
# read the investigation → Approve → the fix really runs → verify polls the real metric
uv run pz demo reveal              # which fault was sealed
uv run pz demo heal                # undo every fault before the next chaos
```

Useful side windows: Prometheus http://localhost:9090/alerts, Alertmanager http://localhost:9093, the app http://localhost:8010/offers/42.

Practice `bad-deploy` (clean win) and `consumer-lag` (right diagnosis, fix too small, verify says **not recovered**; the honest story).

After the call: `uv run pz demo down` (add `-v` to delete data).

Gotchas (full table in GUIDE §10): a second `chaos` right after the first produces no new incident until `pz demo heal` and the alert is green again (Alertmanager `repeat_interval`); a local Redis on 6379 can shadow the demo one (demo uses 6380); ports 5432/5433, 6380, 8000, 8010, 9090, 9092, 9093 must be free.

---

## 4. Numbers you can quote without running anything

- `make test` → 81 tests pass, 1 skipped.
- Benchmark: `uv run pz bench --n 20 --seed 7 --no-memory` (~25–30 min, ~250 LLM requests). Results in `bench/results/*.json`; the README table is copied from the printed summary. Scored against hidden ground truth, no LLM judge: root-cause family, culprit named, fix matches, verified recovery.

---

## 5. If it is ever hosted

Recommended shape, not built yet:

- One small VM (e.g. `t3.small`, ~$15/month) running Docker: `pz serve` in **synthetic** mode + the Postgres/pgvector container, behind nginx with HTTPS and **basic auth** (anyone who can fire incidents burns the LLM quota).
- Incident list and traces readable without login; "fire" and "approve" behind the password.
- The demo stack stays on the laptop: too heavy for a small box and it is a demo by nature.
- Secrets (`.env`) copied to the box by hand or via SSM Parameter Store, same as the ShopEase pattern.
- Work: ~2 hours reusing the ShopEase OpenTofu files. Do it only if a permanent link on the resume is wanted.

---

## 6. Quick answers

- *Where is the frontend?* A single static HTML file served by the FastAPI process at `/ui`.
- *Where is the backend?* The same process: FastAPI + the LangGraph agent + the Alertmanager webhook.
- *Where is the database?* Optional Postgres + pgvector in Docker for memory; runtime state in `.pz/` (incidents, checkpoints.sqlite).
- *Is it on AWS?* No. Laptop only; section 5 is the plan if that changes.
- *What does it cost?* $0 while on the laptop; LLM calls are on free-tier keys (Gemini 500 req/day/model, Groq 200k tokens/day).
