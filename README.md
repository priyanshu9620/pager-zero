# Pager-Zero

An LLM on-call agent that **diagnoses a production incident and prepares the fix** — rollback, config
revert, scale-up — and then waits for a human to approve before anything is executed.

> Status: under construction. Phase 0 (skeleton) done; see `pz --help`.

## Quick start

```bash
uv sync
cp .env.example .env   # set the key for the provider you choose
uv run pz doctor --ping
```

## Choosing a model

Pager-Zero is provider-agnostic. Change two lines in `.env`:

| Provider | `LLM_PROVIDER` | `LLM_MODEL` (example) | Key |
|---|---|---|---|
| Anthropic | `anthropic` | `claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google AI Studio | `google_genai` | `gemini-2.5-flash` | `GOOGLE_API_KEY` |
| Groq | `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Ollama (local) | `ollama` | `qwen2.5:14b` | none |

## Layout

```
src/pager_zero/
  config.py      settings (.env)
  llm.py         the only provider-aware module
  cli.py         `pz`
  adapters/      Protocols + synthetic/live implementations      (phase 1, 7)
  synth/         scenario generators → World + hidden GroundTruth (phase 1)
  mcp_server/    tools exposed over MCP                           (phase 2)
  agent/         LangGraph graph: investigate → diagnose → approve → fix (phase 3)
  memory/        pgvector incident memory                         (phase 4)
  bench/         benchmark runner + scoring                       (phase 5)
  api/           FastAPI                                          (phase 6)
```
