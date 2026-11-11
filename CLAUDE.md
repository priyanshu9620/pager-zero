# CLAUDE.md — pager-zero

Personal resume project of Priyanshu Gupta. **Not a my employer repo.**

## Start here
User-facing manual (setup, commands, UI, demo stack, troubleshooting): `docs/GUIDE.md` — keep it current.
Project memory lives OUTSIDE this repo: `~/Desktop/pager-zero-notes/`.
Read `session/HANDOFF.md` there before doing anything; it points to locked decisions, the build plan,
progress and problems. Update those files as you work (rules in that folder's README).

## Hard rules
- LLM access only via `pager_zero/llm.py` (`init_chat_model`). No provider-specific features anywhere.
- Agent and MCP tools import adapters only through `adapters/base.py` Protocols.
- Mutating tools are reachable only after the LangGraph approval `interrupt()`.
- Nothing from any employer: no internal service/library/flag names, no real incident data.
- Pin exact dependency versions (`==`) in `pyproject.toml`.
- CLI + web approval console (`/ui`, static HTML/JS served by FastAPI — no frontend toolchain).

## Commands
`uv sync` · `uv run pz …` · `make test` · `make lint` · `make fmt`
