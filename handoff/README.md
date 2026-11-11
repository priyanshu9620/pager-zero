# handoff/ — everything needed to continue Pager-Zero from a fresh laptop

Personal project; the owner chose to keep the session notes and (encrypted) secrets in the repo so a clone is
complete. Same layout as the ShopEase repo's `handoff/`.

## What is here

| path | what | restore to |
|---|---|---|
| `env.enc` | the `.env` (LLM provider/model, API keys for Anthropic/Google/Groq/OpenAI, DATABASE_URL, adapter backend), AES-256-CBC (PBKDF2) encrypted with the owner's passphrase | `.env` in the repo root after decrypting |
| `notes/` | project notes: `session/HANDOFF.md` (read first), DECISIONS (D-1…D-41), BUILD-PLAN, PROGRESS, PROBLEMS (P-1…P-32), `kt/` explanations | `~/Desktop/pager-zero-notes/` (or read in place) |
| `claude-memory/pager-zero-project.md` | Claude Code's memory file for this project | the Claude memory folder of the project you open (see below) |
| `../docs/GUIDE.md`, `../INTERVIEW_PREP/`, `../README.md` | the manual, the interview notes, the pitch — already in the repo | — |

## New laptop, step by step

```bash
git clone https://github.com/priyanshu9620/pager-zero.git ~/Desktop/pager-zero && cd ~/Desktop/pager-zero
# tools: Docker Desktop, uv (curl -LsSf https://astral.sh/uv/install.sh | sh), optionally Ollama for the local model
cp -R handoff/notes ~/Desktop/pager-zero-notes
openssl enc -d -aes-256-cbc -pbkdf2 -in handoff/env.enc -out .env        # asks for the passphrase
uv sync
make demo-up && make demo-serve         # or follow docs/GUIDE.md: synthetic mode needs no Docker at all
```

Claude Code memory: open Claude Code in `~/Desktop/pager-zero`, run it once so it creates
`~/.claude/projects/-Users-<you>-Desktop-pager-zero/memory/`, copy `handoff/claude-memory/pager-zero-project.md`
there and add to that folder's `MEMORY.md`:
`- [Pager-Zero project](pager-zero-project.md) — personal AI on-call agent; read handoff/notes/session/HANDOFF.md`.

First prompt for the new session:

> Read handoff/README.md, docs/GUIDE.md and handoff/notes/session/HANDOFF.md, then tell me the current state and what is left.

## Notes on the keys

The free-tier LLM keys in `.env` have daily quotas (Gemini 500 req/day/model, Groq 200k tokens/day);
`docs/GUIDE.md` explains switching providers and the Ollama fallback. If a key is dead, create a new one
at the provider and edit `.env`; re-encrypt with `openssl enc -aes-256-cbc -pbkdf2 -salt -in .env -out handoff/env.enc`.
