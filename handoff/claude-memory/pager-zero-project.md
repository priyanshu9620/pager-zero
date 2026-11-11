---
name: pager-zero-project
description: Pager-Zero resume project (LLM on-call agent) — DONE as of 2026-11-10; where everything lives and what's optional
metadata:
  type: project
---

Pager-Zero: user's personal resume project, repo ~/Desktop/pager-zero (public, github.com/priyanshu9620/pager-zero, main `601fce0`). Notes folder ~/Desktop/pager-zero-notes (session/HANDOFF.md first). User declared it DONE on 2026-11-10 — "we will come back if i need anything more".

**Why:** future sessions must not rebuild or re-litigate; they should read HANDOFF.md, docs/GUIDE.md (user manual) and INTERVIEW_PREP/ (canonical KT) in the repo.

**How to apply:** main contains synthetic + blind mode, benchmark (95/95/95 n=20), real demo stack with sealed chaos (`pz demo`), Ollama support, GUIDE + INTERVIEW_PREP. Optional leftovers: bench re-run post-fix, resume bullet 4 → 95/95, re-plan loop after failed verify, `pz doctor --quota`. Push with the token-header one-off command (P-5) unless the user set up SSH. Never put employer names/data in the repo ([[no-repo-owner-names-in-chat]]).
