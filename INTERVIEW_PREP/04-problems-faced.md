# 04 — Problems I actually hit, and what each taught me

All real. Written as stories, grouped by theme, newest-biggest first. Pick three or four you can
tell well; interviewers care more about the *lesson* than the bug.

## The ones worth telling in an interview

**1. "It worked" — on the wrong world.** The first time I pointed the agent at the real demo stack,
it produced a confident, correct-looking diagnosis… of a *synthetic* incident. The trace mentioned
a service that doesn't exist in the demo. The tool server runs as a subprocess, and the MCP SDK
gives subprocesses only a minimal environment (HOME, PATH…) by default — my `ADAPTER_BACKEND=live`
never arrived, the child read `.env`, defaulted to synthetic, and silently served a generated
world. *Lesson:* a child process that "works on defaults" is the most dangerous misconfiguration;
I now forward the environment explicitly, assert the backend in the trace, and have a regression
test.

**2. Verification said "not recovered" while Prometheus showed full recovery.** On the live stack
the rollback really ran, p99 dropped from 495 ms to 24 ms within a minute — and verify watched for
five minutes and reported failure. Cause: the live tool server pinned "now" at start-up (the
synthetic world *needs* a frozen clock, and the shared code path had a "sensible fallback"). Every
query window ended before the fix. *Lesson:* the synthetic and live worlds need opposite clock
semantics; a fallback that makes both "work" hides the difference. Also: verification must be
tested against a system that actually changes.

**3. The agent's fix was right and still didn't work — and the system said so.** Sealed random
fault: the consumer group had been cut to one slow member. The agent diagnosed consumer-lag
correctly and proposed scaling to 2 members. I approved; it really scaled; but 2 × 150 ms/message
drains 13 msg/s against 20 msg/s incoming. Real lag kept climbing; verify returned not recovered;
the incident closed unresolved with ✔ family, ✔ action, ✘ recovery. *Lesson:* this is exactly why
verification is code and why the design is "prepare, don't act". The obvious next feature is a
re-plan loop after a failed verify.

**4. Free-tier quotas are counted in requests, not tokens — and all of them ran out by lunch.**
Gemini: 500 requests/day *per model*. Groq: 200k tokens/day. One investigation is 7–15 requests;
a 20-incident benchmark is ~250. After a bench and a morning of testing, every free provider was
exhausted and incidents started dying with a bare `ExceptionGroup`. *Lessons:* budget quota per
day like money; keep a second provider in reserve; unwrap exception groups to the real cause and
write the full traceback per incident; and have a zero-quota fallback — I set up a local 14B model
via Ollama with a two-line config change. It solved a blind synthetic incident 3/3 in under two
minutes.

**5. The catalogue leaked an internal word and the model used it.** One benchmark miss: the agent
proposed `set_flag(value="auto")`. "auto" was my internal type tag meaning "coerce to the flag's
previous type", rendered straight into the prompt's action catalogue. *Lesson:* everything in a
prompt is a suggestion to the model; never leak internal enum names. Fixed by rendering it as
"the flag's previous value (bool/int/str)".

**6. Right cause, wrong label.** Another miss: a flag set TTL to 0, which caused a cache-miss
storm; the agent nailed the cause and the fix but labelled the family `cache-eviction` instead of
`flag-regression`. My taxonomy was ambiguous between trigger and symptom. *Lesson:* define the
label as *the trigger*, with examples, in the prompt — and keep the miss in the published numbers
rather than re-running until it passes.

**7. A fake client proved I'd called the API I *thought* existed.** My Kafka adapter tests used a
fake admin client mirroring my memory of the library. The first smoke test against real Redpanda
failed: the library had renamed `list_consumer_group_offsets` → `list_group_offsets` and changed
the return shape. *Lesson:* a fake only proves you called what you think the API is; every
adapter gets one real smoke test.

**8. One bad tool output blanked the whole UI.** After I changed `get_diff` to return a dict with
a note instead of raising, the UI's summary code did `'last' in d[firstKey]` on a string, threw,
and the exception escaped `render()` — the detail pane went silently empty for every live incident.
*Lesson:* a renderer that builds one big string must isolate failures per row; I wrapped each
summary in its own try/catch and wrote a node script that runs the page's JS against the real API
for several incidents.

## Smaller ones, grouped

**Environment / tooling**
- System Python was 3.9; `uv python install` failed because `~/.local` was root-owned from an old
  sudo install → used Homebrew 3.11 and pinned it.
- Pushing to my personal GitHub from a laptop wired to another account: the system credential
  helper only knew one login, and bearer headers are rejected by git-over-HTTPS. One-off push via
  a basic `x-access-token` header; durable fix is an SSH key + host alias.
- `psycopg3`'s `with conn:` closes the connection on exit (unlike psycopg2) → DDL ran, next
  statement failed with "connection is closed".
- A Homebrew Redis on 6379 shadowed the Docker Redis → the adapter read `maxmemory 0`. Moved the
  container to 6380. `lsof -iTCP:6379` before trusting localhost.
- Docker Desktop wasn't running; Prometheus didn't reload a bind-mounted config until restarted;
  Prometheus overwrote my exporter's `instance` label (needed `honor_labels: true`).
- Alertmanager inside Docker couldn't reach `pz serve` bound to 127.0.0.1 → bind 0.0.0.0.

**Model / provider behaviour**
- Model names drift fast; the ones I remembered were gone. Listed live catalogues.
- Gemini returns message content as a list of blocks, Groq/Anthropic as a string → normalise.
- Groq rejects a tool message whose content is an empty list → never return a bare `[]` from a
  tool; wrap as `{count, items, note}`.
- The newest Gemini flash model took > 90 s per tool-calling turn (hidden reasoning); flash-lite
  took 1.4 s and was accurate enough. *"Newest" ≠ "best for loops".* I time one tool-calling turn
  before choosing a model.
- Groq's free tier (8k tokens/min) is simply too small for a 12-step agent.

**Design mistakes I corrected**
- Two Protocols both had a method called `history` and one class implemented both → renamed to
  `cache_history` / `flag_history`. Structural interfaces share a namespace.
- The read-only tool process never saw the healed world (each process had its own copy) → reload
  on modification time before every read; "now" comes from the state.
- A helper compiled an empty graph just to read a checkpoint; LangGraph refused. The API test had
  mocked that function, so the bug was invisible until the UI called it. *A mocked-out function is
  an untested function* — every seam gets one real test.
- Ratio recording rules (error/total) produce *no* series while nothing has failed, so the agent
  saw "no data" instead of 0 → pre-create the error-labelled counters at zero.

## If asked "what would you do differently?"

Start the live backend earlier. The synthetic world made the agent measurable, but three of the
most instructive bugs (environment forwarding, the frozen clock, ratio rules with no series) only
exist when a real system is on the other side. I'd build the demo stack in week one and run both
backends in CI from the start.
