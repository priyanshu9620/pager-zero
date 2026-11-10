# 02 — Features, one paragraph each

Grouped the way I'd describe them in an interview. Each has the *why* — that's what gets asked.

## A. The agent

**Investigation loop with a hard step cap.** The model is given the alert and the read-only tools
and asked to find the cause. Every tool call is one step; at 12 it is told the budget is exhausted
and must conclude with what it has. *Why:* an unbounded loop is both a cost risk and an
observability risk; a cap turns "the agent is thinking" into a predictable, logged sequence.

**Structured diagnosis.** Output is a typed object — family, root cause sentence, confidence,
evidence list, ruled-out list — not prose. *Why:* the benchmark can score it, the UI can render
it, and "ruled out" forces the model to show negative evidence (no deploy in the window is
evidence too).

**One fix, from a catalogue.** The plan step chooses exactly one action from seven
(`rollback_deploy`, `set_flag`, `scale_consumers`, `restart_consumer_group`, `set_cache_limit`,
`rate_limit_key`, `enable_fallback`) with typed arguments, a rationale, a risk level and a blast
radius. No fix is proposed if the family is unknown or confidence < 0.3. *Why:* an on-call fix
should be a single, reversible, nameable action a human can approve in one glance.

**Human-in-the-loop approval as a graph interrupt.** After planning, the graph *pauses*. State is
checkpointed to SQLite; `pz approve <id>` (or the UI, or the API) resumes it — minutes or hours
later, from a different process. *Why:* "prepare, don't act" is the product's core promise, and an
interrupt is the correct primitive for it: the process can die and the incident survives.

**Mutating tools are not registered until approval.** The investigation session opens the MCP
server *without* `--actions`; the seven mutating tools don't exist in that process. A second
session with `--actions` is opened only inside the execute step. *Why:* defence in depth — the
model cannot even *see* the dangerous tools while it is reasoning.

**Verification by code.** After execution, the alert's key metric (p99 for latency alerts, lag
for lag alerts, error rate for error alerts) is re-queried; recovered means ≤ 1.5× baseline *and*
< 50 % of peak. On live systems it polls every 15 s for up to 5 min. *Why:* the agent must not
grade its own work; a wrong fix shows up as "not recovered", which is what happened in testing.

**Memory / RAG.** Resolved incidents (alert + diagnosis + fix + outcome) and six seed runbooks
are embedded with a local MiniLM model into Postgres + pgvector (HNSW, cosine). The recall step
injects the top-3 as hypotheses. Three backends: pgvector, in-process, off. *Why:* real on-call
knowledge is "we saw this last month"; injecting it as *hypotheses, not proof* keeps the model
from pattern-matching blindly. Benchmarked with memory off to measure reasoning.

**Model-agnostic by construction.** One factory (`init_chat_model`), only tool calling + text +
structured output, no provider-specific features. Tested on Gemini, Groq, Anthropic, Ollama.
*Why:* I knew I'd lose access to any particular provider; and it makes "same agent, local model"
a demonstrable claim.

## B. The tools

**MCP server with LLM-shaped outputs.** Sixteen read tools; outputs are pre-digested — a series
becomes {first, last, min, max, change %, first-deviation time, 16-point trace}; logs become
{counts, top messages, sample}; lists are wrapped as {count, items, note}. *Why:* raw series are
thousands of points; the model needs the shape and the change point, and some providers reject
empty lists.

**Adapters behind Protocols.** Nine reader Protocols (metrics, logs, messaging, cache, datastore,
SCM, deploys, flags, topology) plus a gated Actuator. The tools and the agent import only the
Protocols. *Why:* this is what "pluggable adapters for any backend" means concretely — the
synthetic backend and the live backend are interchangeable at config time.

## C. The synthetic world

**Seeded incident generator.** Six families with variants; `(family, seed)` deterministically
builds a self-consistent world: topology, baselines, deploys, commits with diffs, flags with
history, metric series, logs, consumer groups, caches, tables, dependency health — and one
planted cause with a hidden `GroundTruth`. Red herrings included (an unrelated deploy hours
earlier, an old flag flip, a harmless sibling commit). *Why:* you can only score accuracy against
a well-defined hidden answer; determinism makes runs reproducible across processes.

**Self-healing worlds.** When the *correct* action is applied, the affected series return to
baseline over the next minutes of world time. *Why:* so verification is real even in the
synthetic case — the world isn't told "resolved", it is changed.

**Blind mode.** The server picks family + seed and hides both from the user *and* the agent until
the incident is over; then the planted truth is revealed next to the agent's answer with ✔/✘ on
root cause, culprit, fix and recovery — scored by the same code as the benchmark. *Why:* a demo
has to be falsifiable in front of the viewer.

**Benchmark.** N incidents round-robin over families, auto-approved, scored deterministically
(family match, culprit named in the text, fix matches with ≥/≤ semantics for sizes, verified
recovery), JSON results, aborts after three consecutive infrastructure errors. *Why:* it is the
only way to know whether a prompt change helped.

## D. The real system

**Demo stack.** Nine containers: a FastAPI service on Postgres and Redis, a Kafka consumer group
on Redpanda, a load generator, a mock payment dependency, a small exporter, Prometheus with
recording and alert rules, Alertmanager wired to the agent's webhook. *Why:* "works on real
telemetry" needs real telemetry; recording rules emit the same metric names the agent knows, so
not a line of agent code changes between worlds.

**Chaos with a real actuator.** Five faults do real damage (redeploy with slow code, zero the
cache TTL flag, shrink Redis to 2 MB, cripple the consumer group, stop the dependency). The
actuator's actions are the literal inverses (restart the container on the old version, write the
flag, `CONFIG SET`, `--scale`, flip the fallback flag). *Why:* so approve means something, and so
verification can fail honestly.

**Sealed random chaos.** `pz demo chaos` picks a fault, injects it, and seals the name in a file;
the UI reveals it only when the incident closes. *Why:* blind mode for the real stack.

**Live alert ingress.** Alertmanager's webhook payload creates a live incident; repeat
notifications for an alert already being handled are ignored. *Why:* the natural entry point in
production; dedupe because Alertmanager re-sends.

## E. Surfaces

**CLI** (`pz`) for everything; **web console** (one static HTML file, no build step) with a
pipeline tracker, per-tool summaries and sparklines, approve/reject, ground-truth card; **HTTP
API**; **MCP** (`pz mcp serve`) so other agents can use the tools; **Alertmanager webhook**.
