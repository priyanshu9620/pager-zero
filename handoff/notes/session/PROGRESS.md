# Progress ledger

Newest first. One block per working session.

## 2026-11-10 — session 2 (cont.) — Phase 11 demo stack on branch `feat/demo-stack` (v2)
- User: "all incidents are pre-known" → agreed closed-world bench vs open-world live; chose option (b) real stack on a **separate branch**, merge only if it works (main untouched at `141dc51`).
- Built `demo/` (9 containers), `pz demo up|down|status|chaos|heal`, `DemoActuator`, live alert ingress + dedupe, verify polling, env forwarding to MCP subprocess, errors.py + error.txt, UI live labels, demo/README.md. 79 tests.
- Six live rounds to get the proof: R1 synthetic world served (P-26) · R2 Gemini 429 (P-29) · R3 Groq TPD (P-30) · R4 Ollama → `unknown`, no fix (honest miss) · R5 Gemini correct + real rollback but verify frozen (P-31) · **R6 Gemini: full success, resolved in 70 s after approval** (inc_45a8b32d).
- Ollama installed (`qwen2.5:14b`, D-40); blind synthetic downstream-outage 3/3 in 1:54.
- Branch pushed; stack left RUNNING (`pz demo down` to stop); `pz serve` on :8000 (gemini-3.5-flash-lite, live backend) running.
- Sealed random chaos (D-41) built + run: `pz demo chaos` sealed **consumer-lag**; alert ConsumerLagHigh → inc_21e8a611 → agent: consumer-lag ✔, `scale_consumers(members=2)` — right action but **under-sized** (2×150 ms = 13 msg/s < 20 msg/s in) → real lag kept growing → verify **unresolved** after 300 s; reveal card: ✔ family ✔ action ✘ recovery. Best falsifiability demo so far: the system, not the model, said no.
- User asked for a self-contained manual in the repo (so no Claude session is needed to run it) → `docs/GUIDE.md` (prereqs, LLM choice incl. Ollama, install, synthetic mode, UI panel-by-panel, demo stack + sealed chaos, own infra, bench, every command, troubleshooting, where things live); linked from README + CLAUDE.md.
- **MERGED to main** (`68718b4`, user: "no need of v1 v2, merge everything"); branch deleted. main = synthetic + blind + demo stack + sealed chaos + Ollama + docs/GUIDE.md. 81 tests.
- `INTERVIEW_PREP/` pushed to main (`601fce0`): 01 what-it-is (30s/2min/5min pitches, can/cannot) · 02 features with why · 03 how-it-works (concepts) · 04 problems faced (real P-1..P-32 as first-person stories, no inventions) · 05 codebase map + integrations + "how to add X" · 06 Q&A incl. honest ones. Verified no employer references anywhere in the repo. This supersedes `kt/` in the notes folder for interview prep — keep both in sync if either changes.
- **Next:** then: bench re-run (quota permitting, evening), Gemini-vs-Ollama bench row, resume bullets (now can say "validated on a containerised stack with chaos-injected faults"), `pz doctor --quota`.

## 2026-11-10 — session 2 — UI v2, repo public, blind mode
- UI v2 pushed `0000545` (pipeline tracker, sparklines, per-tool summaries, confidence bar). Repo made **public** (D-17 superseded: https://github.com/priyanshu9620/pager-zero).
- User: "whole project looks fake — pre-fed, can't make a new incident". Answer: blind mode now (D-37), real demo stack next (D-38). Blind mode built + verified live (inc_857e3bdf: hidden → agent cache-eviction → reveal cache-eviction #4441, 3/3 ✔). 62 tests.
- Server running `pz serve --port 8000` on `gemini-3.1-flash-lite` (3.5 quota still exhausted until ~13:30 IST).
- **Next:** user reviews blind UI → decide on Phase 11 demo stack; bench re-run after quota reset; resume bullet 4.

## 2026-11-09 — session 1 (cont.) — Phase 9 web UI DONE
- User asked to continue with the UI → built the approval console (D-36), 59 tests, pushed.
- Resume: user wants SHORT lines (one page). Agreed: keep original lengths; add "behind human approval", "pgvector"/"RAG over past incidents (pgvector)", "Redis", optionally "any LLM provider"; bench line updated after tomorrow's run. "23 tools" dropped from resume (interview trivia).
- Memory layer = RAG (retrieve top-3 past incidents/runbooks by embedding → inject as hypotheses → generate); bench measured with it OFF.
- Server left running: `pz serve --port 8000` with `gemini-3.1-flash-lite` (the only Gemini model with quota left today). UI at http://127.0.0.1:8000/ui.

## 2026-11-09 — session 1 (cont.) — Phases 6, 7, 8 (most) DONE
- User: stop bench re-runs; build 6–8; data later (fallback n=10) → D-34.
- Phase 6 FastAPI + webhook (52 tests) `c266cfb`; Phase 7 live adapters + live docker profile, smoke-tested against real Prometheus/Redis/Redpanda `10910d3`; Phase 8 README/CI/LICENSE pushed. 58 tests green offline.
- Repo is still private. Docker: postgres + live profile containers are up (`docker compose --profile live down` to stop them; keep postgres for memory).
- **Resume wording proposal (Pager-Zero project, 4 bullets, truthful today):**
  1. Built an LLM on-call agent that investigates production alerts across metrics, logs, deploys, flags, Kafka, cache and dependency signals, diagnoses the root cause with cited evidence, and prepares the fix (rollback, flag revert, scale-out…) behind human approval.
  2. Exposed 23 tools as an MCP server; LangGraph agent with a step-capped loop, SQLite-checkpointed approval interrupt, and code-based verification of recovery; pgvector memory of past incidents and runbooks (local embeddings).
  3. Built a seeded synthetic incident generator (6 failure families, variants + red herrings, self-healing worlds) and pluggable adapters (Prometheus, Kafka, Redis, GitHub, Loki) behind Python Protocols; provider-agnostic (Claude / Gemini / Groq / Ollama via config).
  4. Benchmarked on 20 generated incidents: 95% root-cause and 95% correct-fix accuracy (verified recovery) on a free-tier Gemini model, ~8 tool calls and 34 s per incident.
- **Next session:** (a) decide public/private; (b) bench re-run when quota resets (post-fix) + stronger model row; (c) optional asciinema GIF; (d) web approval UI is the deferred phase 2 of UX.

## 2026-11-09 — session 1 (cont.) — Bench run 1 + fixes, run 2 started
- **Run 1 (gemini-3.5-flash-lite, n=20, seed 7, memory off): root cause 19/20 (95%), culprit named 19/20, fix 19/20 (95%), verified 19/20; avg 7.7 steps, 34 s, 33k in / 0.4k out tokens.** File `bench/results/google_genai__gemini-3.5-flash-lite__20261109T153333.json`, commit 7e08491.
- Misses: consumer-lag#7002 culprit text lacked the group name (fair); flag-regression#7010 right cause/fix, family labelled cache-eviction (P-20); flag-regression#7016 `value="auto"` — my catalogue bug (P-19). Both fixed; run 2 started (`/tmp/pz_bench2.log`).
- **Next:** read run 2 → README bench section (both runs listed) → resume numbers → Phase 6.

## 2026-11-09 — session 1 (cont.) — Phase 4 DONE, Phase 5 code DONE
- User approved Phase 3; asked for 4 and 5.
- Docker Desktop was off → launched; pgvector up; psycopg3 `with conn` closes the connection (P-17) fixed.
- Memory live: runbooks loaded, consumer-lag#7 recalled the right runbook first, resolved (scale 1→24, risk=medium stated honestly). Memory count 9.
- Bench code + 50 tests green; pushed `a6af314`. First bench run started: `pz bench --n 20 --seed 7 --no-memory --pause 3` on gemini-3.5-flash-lite (log `/tmp/pz_bench1.log`).
- **Next:** read results, write README bench table, decide resume numbers, Phase 6.

## 2026-11-09 — session 1 (cont.) — Phase 3 DONE
- Agent built (graph/runner/schemas/prompts/CLI), 41 tests green, pushed.
- Live runs on gemini-3.5-flash-lite: bad-deploy#42 → correct commit + rollback; cache-eviction#7 → set_cache_limit; both `resolved` with verify showing real recovery. Trace files prove the audit trail.
- Free-provider findings (P-11..P-14): Groq 8k TPM too small; Gemini 3.8-flash >90 s/call; flash-lite 1.4 s/call and good enough; Groq rejects empty-list tool results → lists wrapped as {count, items}.
- `.env` now defaults to google_genai/gemini-3.5-flash-lite.
- **Next:** Phase 4 memory (pgvector), then Phase 5 bench (the resume numbers).

## 2026-11-09 — session 1 (cont.) — Phase 2 DONE
- User reviewed Phase 1 worlds: approved; will do manual testing later and feed back changes.
- MCP server built, 38 tests green, pushed. Tool outputs are deliberately LLM-shaped (series → stats + change point + 16-pt trace; logs → counts + top messages) to keep context small (P-9).
- **Next:** Phase 3 — `agent/state.py`, `agent/graph.py` (triage → recall(stub) → investigate loop ≤ MAX_STEPS → diagnose → plan_fix → interrupt → execute → verify), SQLite checkpointer, JSONL trace, `pz incident fire` / `pz approve` / `pz reject` / `pz status`.

## 2026-11-09 — session 1 (cont.) — Phase 1 DONE
- Gemini + Groq keys added to `.env`; working models: `gemini-3.8-flash` (slow first call), `gemini-3.5-flash-lite` (2 s ping), `openai/gpt-oss-120b` on Groq (P-8).
- Phase 1 built and pushed: adapter Protocols, World + GroundTruth, WorldBuilder, 6 scenarios with variants, synthetic backend with self-healing, `pz world show`, 33 tests green.
- Sanity-checked `bad-deploy --seed 42`: deploy v1.60 at 11:06:24, two commits (one harmless, one N+1), p99 250→1090 ms, db_qps 6×, cpu 0.4→0.9, pool-wait flat, 742 logs. Realistic enough to proceed.
- **Next:** Phase 2 — MCP server (FastMCP, stdio) exposing the readers as tools and the actuator as a separately-tagged mutating group; `pz mcp serve`.

## 2026-11-09 — session 1 (kickoff) — Phase 0 DONE
- Understood resume bullets; locked D-1…D-16.
- Created notes folder (this).
- Phase 0 skeleton at `~/Desktop/pager-zero`: uv project (py3.11), pinned deps (langgraph 1.2.20, langchain 1.4.9, mcp 1.30.0, langchain-mcp-adapters 0.3.2, fastapi 0.162.2, typer 0.28.0, sentence-transformers 6.2.0, pgvector 0.5.1, …), `config.py`, `llm.py`, `cli.py` with `pz doctor`, `.env.example`, README, Makefile, CLAUDE.md pointing here. Ruff clean.
- Verified: `uv run pz doctor` prints config and exits 1 until a key is set (expected).
- `.env` written (Anthropic key; Gemini/Groq/OpenAI placeholders). `load_dotenv()` added (P-7). Ping reaches Anthropic but org has **no credits** (P-6) — user to top up or add a free-provider key.
- First commit `6f7d225` pushed to `github.com/priyanshu9620/pager-zero` (private) with personal identity (D-17, P-5).
- **User to-do (optional, durable personal-push setup):**
  ```bash
  ssh-keygen -t ed25519 -C "guptapriyanshu1822@gmail.com" -f ~/.ssh/id_ed25519_personal
  printf '\nHost github-personal\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/id_ed25519_personal\n  IdentitiesOnly yes\n' >> ~/.ssh/config
  GH_TOKEN=$(gh auth token --user priyanshu9620) gh ssh-key add ~/.ssh/id_ed25519_personal.pub --title "macbook personal"
  cd ~/Desktop/pager-zero && git remote set-url origin git@github-personal:priyanshu9620/pager-zero.git
  ```
  Until then, Claude pushes with the one-off token-header command from P-5.
- **Next:** Phase 1 — `adapters/base.py` Protocols, `synth/world.py`, six scenario generators, `adapters/synthetic.py`, `pz world show`.
