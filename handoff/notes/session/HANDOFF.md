# HANDOFF — read this first

**Project:** Pager-Zero — LLM on-call agent that diagnoses a production incident and *prepares* (never auto-applies) the fix. Resume project for Priyanshu Gupta.
**Code:** `~/Desktop/pager-zero` · **Notes:** `~/Desktop/pager-zero-notes` (this folder).

## Where we are
**2026-11-10 evening: user declared the project DONE** ("we will come back if i need anything more"). main = `601fce0`, CI green, repo public, demo stack + server stopped, pgvector container still defined (`docker compose up -d` to use memory). Open-but-optional: bench re-run post-fix (+ Ollama row), resume bullet 4 numbers (use 95/95), re-plan loop, `pz doctor --quota`, demo GIF, SSH push setup.
See top block of `PROGRESS.md`. **All build phases 0–10 are done and pushed; repo is PUBLIC** (2026-11-10). Phase 11 demo stack (D-39/D-41) is merged into `main` (2026-11-10). Remaining: bench re-run post-fix + stronger-model row when quota allows (D-34; fallback n=10), optional demo GIF. Resume wording proposal is in `PROGRESS.md` (top block).
Docker must be running for pgvector (`docker compose up -d`; `pz memory init` once). Without it memory degrades to no recall.
Working free model: `google_genai` / `gemini-3.5-flash-lite` (set in `.env`). Groq free tier is too small (8k TPM) for the agent; Gemini 3.8-flash too slow (>90 s/call). Anthropic key has no credits (P-6).
Demo: `uv run pz incident fire` (blind random, D-37) → `uv run pz approve <id>` → `pz incident show <id>` reveals ground truth. Or `pz serve` → http://127.0.0.1:8000/ui.
Phase 4 shape: `memory/store.py` (Postgres+pgvector via docker compose, local MiniLM embeddings), index resolved incidents (diagnosis + plan + alert) and runbooks, `recall` node queries top-3 by alert+symptom text. Must degrade gracefully when Postgres is down (recall → []).

## How to work
0. The repo now carries its own manual (`docs/GUIDE.md`) and the interview KT (`INTERVIEW_PREP/`, 6 files — the canonical version; `kt/` here is the older draft). Any change to commands, UI, config or the demo stack must update it (the user relies on it when no Claude session is around).
1. Read `DECISIONS.md` — do not re-litigate anything there; if something must change, append a new decision that supersedes it.
2. Follow `BUILD-PLAN.md` phase order. Each phase must end runnable.
3. Log problems in `PROBLEMS.md`, progress in `PROGRESS.md`, and keep `kt/` in sync for anything interview-relevant.
4. Code constraints that matter most: provider-agnostic LLM access (D-3), adapters behind Protocols (D-8), approval interrupt before any mutating tool (D-12), no my employer internals anywhere (D-9).

## Resume bullets this must satisfy (verbatim)
- Built an LLM agent that diagnoses production incidents and prepares the fix (config revert, rollback, etc.).
- Exposed tools as an MCP server driven by a LangGraph agent with a step-capped loop and human-in-the-loop approval.
- Built a synthetic incident generator and pluggable adapters (Prometheus, Kafka, GitHub) for any backend.
- Benchmarked at 92% root-cause accuracy, 85% correct-fix, over 20 generated incidents. ← numbers to be re-measured (D-11).

## Stack
Python 3.11 (uv, Homebrew interpreter — see P-2) · LangGraph · LangChain `init_chat_model` · `mcp` (FastMCP) · `langchain-mcp-adapters` · FastAPI · Typer+Rich CLI · Postgres+pgvector · sentence-transformers · Docker Compose.
