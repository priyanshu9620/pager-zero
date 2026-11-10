# 03 — How each piece works (concepts, not code)

## 1. The request lifecycle, end to end

```
alert ──► triage ──► recall ──► investigate ⟲ tools (≤12) ──► diagnose ──► plan_fix ──► ⏸ approval
                                                                                          │
                      verify ◄── execute ◄──────────────────────────────── approve ◄──────┘
```

- **triage** is deterministic: call `incident_context` (alert, now, window, services) and
  `service_info` for the alerting service. No LLM.
- **recall** embeds "alert name + service + summary" and asks the memory store for the top-3
  similar past incidents/runbooks. Injected into the prompt as *hypotheses*.
- **investigate** is the LLM with the read tools bound. Each turn either calls tools (→ the
  `tools` node runs them and loops back) or writes text (→ exit to diagnose). A counter in the
  state increments per tool call; at the cap the model gets a "budget exhausted, conclude now"
  message.
- **diagnose** is a structured-output call: the transcript in, a `Diagnosis` object out.
- **plan_fix** is another structured-output call with the action catalogue in the prompt. Skipped
  if family is `unknown` or confidence < 0.3 → incident ends `unresolved`.
- **approval** calls LangGraph's `interrupt()`. The graph's state is checkpointed; the process
  returns. Resuming with `{decision: approve|reject, note}` continues from here.
- **execute** opens a *second* MCP session with `--actions`, calls the one tool named in the
  plan, records the result.
- **verify** picks the key metric from the alert name, re-queries it, applies the thresholds,
  optionally polls, and sets the final status.

Everything the agent saw and did is appended to a JSONL trace per incident — the audit trail the
UI renders.

## 2. Why MCP, and how the two sessions work

MCP (Model Context Protocol) is a standard way to expose tools over stdio/HTTP so *any* client can
call them. I run the tool server as a subprocess over stdio. The server has a flag: without
`--actions` it registers only the 16 read tools; with it, also the 7 mutating ones. The agent's
investigation session never passes the flag. So "the agent can't act without approval" is not a
prompt instruction — it's a process boundary.

Because the server is a separate process, state shared between the read session and the actions
session (in the synthetic case) goes through a file with modification-time checks: after the
actions process heals the world and saves it, the read process notices the newer file on its next
tool call and reloads. The system's "now" also comes from that state, not the wall clock —
synthetic worlds have their own frozen clock. (The live backend must do the opposite: never pin
"now". Getting that wrong was one of my bugs — see 04.)

## 3. How the synthetic generator produces a believable incident

A `WorldBuilder` seeded with `random.Random(f"{family}:{seed}")` first builds a **healthy
baseline**: six fictional services with versions, replicas, dependencies, consumer groups, caches,
tables and flags; flat-ish metric series with noise over a 45-minute window; routine commits;
chatter logs. Then the family's **recipe** mutates it: picks a target (which service, which cache,
which variant), chooses a cause time `t0`, steps or ramps the affected series (p99 ×4.8, DB QPS ×7,
hit-rate 0.96 → 0.38, lag growing, error rate to 60 %…), writes the log burst, adds the deploy /
flag change / commit that is the real cause, adds red herrings, registers a **recovery plan**
(which series return to which baseline when fixed), builds the alert that would have fired, and
declares the `GroundTruth` (family, cause sentence, culprit fields, expected action + args).

The adapter that serves this world implements every reader Protocol by filtering those structures
by time window and labels — so `query_metrics` on the synthetic backend and on Prometheus return
the same shape.

Two properties matter: **internal consistency** (everything derives from one `t0`, unrelated
signals stay flat, so the cause is *derivable*) and **determinism** (same seed, same world, in
every process — which is what lets the ground truth be recomputed instead of stored).

## 4. How the live backend maps real systems onto the same tools

| Protocol | live implementation | note |
|---|---|---|
| MetricsReader | Prometheus HTTP API `query_range` | metric names must exist — I use recording rules to produce `http_p99_ms`, `db_qps`, `cache_hit_rate`, `dependency_error_rate`… |
| LogsReader | Loki `query_range` | optional; empty if unset |
| MessagingReader | Kafka admin API: committed offsets vs end offsets per partition, group members | history via a `kafka_consumergroup_lag_sum` series |
| CacheReader | Redis `INFO` (maxmemory, used, hits/misses, keys) | history via `redis_*` series |
| SCMReader | GitHub REST (commits since, diff) | per-service repo from the topology file |
| DeployReader / FlagReader | JSON files your CI / flag service append to | the plug point, not an integration |
| TopologyReader | static JSON | services → deps / groups / caches / tables / flags |
| Actuator | **dry-run** (logs the intent) unless `ACTUATOR=demo` | never touches someone's infra by default |

The demo stack makes this concrete: a real FastAPI service exports a request-duration histogram;
Prometheus computes `histogram_quantile(0.99, …)` into `http_p99_ms`; the agent's `query_metrics`
reads exactly that.

## 5. How verification decides

Key metric by alert name: `HighLatencyP99` → `http_p99_ms{service}`, `ConsumerLagHigh` → lag of
the group in the alert's labels, `HighErrorRate` → `http_error_rate{service}`. Query the last 60
minutes; baseline = first point, peak = max, now = last. Recovered ⇔ `now ≤ max(1.5 × baseline,
ε)` **and** `now < 0.5 × peak` (lag uses 2× tolerance). Live backends re-check every 15 s until
recovered or timeout. The agent has no input here.

## 6. How memory works

Embedding model: `all-MiniLM-L6-v2` (384-d, runs locally, no API). Table `memories(kind, title,
body, meta, embedding vector(384))` with an HNSW cosine index. **Remember**: after an incident
resolves with a real diagnosis, store "alert + symptoms + family + fix + outcome". **Recall**:
embed the triage text, `ORDER BY embedding <=> query LIMIT 3`, return title/kind/similarity and a
short body. Runbooks are the same rows with `kind=runbook`. If Postgres is down, recall returns
empty and the agent proceeds — graceful degradation by design.

## 7. How the benchmark scores without an LLM judge

For each incident: regenerate the world from `(family, seed)` to get the ground truth; then
`root_cause_ok = diagnosed family == planted family`; `culprit_ok = every culprit identifier
(version / commit / flag / group / cache / key / dependency) appears in the diagnosis text`;
`fix_ok = proposed action == expected action and every expected arg matches (≥ for sizes, ≤ for
rate limits)`; `verified = the verify step said recovered`. Also records steps, seconds, tokens.
Round-robin over families; seeds `seed*1000+i`; results JSON per run.

## 8. How the UI stays simple

One static HTML file served by FastAPI; polls `GET /incidents` and `GET /incidents/{id}` +
`/trace` every 2 s; renders summaries by inspecting each tool result's shape (a series →
sparkline + first→last; logs → count + top message; lists → count + first item). No framework,
no build. Approve/Reject are two POSTs.

## 9. How sealed chaos reveals the truth on a real system

`pz demo chaos` picks a fault, applies it, and writes `{fault, detail, at}` to a sealed file. The
CLI never prints the fault. When a live incident reaches a terminal state, the API checks: is
there a seal, and was it sealed *before* this incident opened? If so it reveals it and scores
family equality and whether the proposed action is in the fault's expected-fix set. `heal` clears
the seal. Same falsifiability principle as blind mode, on real infrastructure.
