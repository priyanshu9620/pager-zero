# pager-zero-notes

Memory folder for the **Pager-Zero** project (code lives in `~/Desktop/pager-zero`).

Two audiences, two sub-folders:

| Folder | For | Style |
|---|---|---|
| `kt/` | Priyanshu — read before interviews / when asked "why did you build it this way" | Few files, short, plain language |
| `session/` | Claude (any session, now or months later) — pick the project back up | Precise: locked decisions, progress ledger, problems hit, next steps |

**Rules for Claude sessions**
- Read `session/HANDOFF.md` first — it says where we are and what to do next.
- Every locked decision goes in `session/DECISIONS.md` (append, never rewrite history; mark superseded ones).
- Every non-trivial problem + fix goes in `session/PROBLEMS.md`.
- Update `session/PROGRESS.md` at the end of each working session.
- When a decision matters for the interview story, also add a one-paragraph plain-English version to the matching `kt/` file.
- Never put my employer internal names (services, libraries, flag keys, people) in either folder's content that could end up in the public repo. The notes folder itself is private, but keep it clean anyway.
