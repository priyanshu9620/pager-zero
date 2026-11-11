# 04 · Interview FAQ

Grows as we build. Keep answers to 3–5 sentences.

**Q: Why an agent and not a rules engine / runbook automation?**
Runbooks cover known shapes; the first 20 minutes of a real incident is *deciding which runbook applies*. The agent does that correlation (deploy time vs. spike, lag vs. pod count, flag flip vs. error onset) across heterogeneous tools. The fix itself is still a fixed, auditable action from a small catalogue — the LLM picks, it doesn't improvise.

**Q: How do you stop it from doing something dangerous?**
Three layers: mutating tools are physically unreachable before the approval interrupt; the loop has a hard step cap; every tool call is written to an audit trail. The agent *prepares* a fix; a human *executes* it.

**Q: How do you know it works? Isn't 20 incidents tiny?**
Yes, and I say so. The bench scores root-cause category and fix action against a hidden ground truth on seeded, reproducible incidents. 20 is enough to catch regressions when I change prompts; it is not a statistical claim. The generator can produce hundreds — cost was the limiter.

**Q: What does the memory actually do?**
Every resolved incident (alert + root cause + fix + outcome) and a handful of generic runbooks are embedded with a local MiniLM model and stored in Postgres with pgvector. When a new alert comes in, the `recall` node fetches the three most similar entries and hands them to the agent as *hypotheses, not proof* — it still has to gather evidence. In a consumer-lag incident the first hit was the "consumer lag growing" runbook, and the agent went straight to members / pods_ready / OOM logs. The benchmark runs with memory off so the number is a cold-start measurement; memory lift is measured separately.

**Q: What's "correct-fix" exactly?**
The proposed action matches the ground-truth action type and target (e.g. `rollback_deploy(checkout-api → v1.56)`), judged by exact match on action + target; wording differences in the explanation don't count.

**Q: Why MCP instead of just Python functions?**
Decoupling and reuse: the same tool server works from Claude Code, from my agent, from a future web UI. It also forced a clean read/mutate split at the protocol level: mutating tools are only registered when the server is started with `--actions`, so the investigating agent literally cannot call them — the gate is in the process, not in the prompt.

**Q: How do you keep the agent's context small across a 12-step investigation?**
Tool outputs are pre-digested on the server side. A metric query returns first/min/max/last, the percentage change, the timestamp of the first deviation from baseline, and a 16-point trace — not 90 raw samples. A log search returns the total count, the top repeated messages with first-seen times, and a small sample. The model gets the signal, not the dump.

**Q: How does the synthetic generator avoid being trivially easy?**
Three ways. (1) Each family has *variants* — a bad deploy can be a pool shrink, an N+1 query, or a sequential upstream call, and each leaves a different metric/log trail, so the agent must actually read the evidence. (2) *Red herrings*: an unrelated deploy 20 minutes earlier, a harmless sibling commit in the same release, a flag flipped hours ago. (3) The flat signals matter: in a hot-partition incident there is no deploy and no flag change, and the agent should notice that absence.

**Q: How does the agent know its fix worked?**
The synthetic world reacts. The correct action makes the world "heal": time advances three minutes and the affected series return to baseline, so the verify step re-queries metrics and sees a genuine recovery. A wrong action is recorded but nothing improves. That means verification is a real check, not a rubber stamp.

**Q: Walk me through one run.**
`pz incident fire -s cache-eviction --seed 7`. Triage pulls the alert (HighLatencyP99 on offer-service) and its topology. The agent queried p99, checked deploys (none) and flag history (unchanged) — ruling those out — then cache hit-rate (0.98 → 0.39), evictions (0.5 → 18k/s) and warn logs ("maxmemory 8192MB reached"). Diagnosis: cache-eviction, confidence 1.0, with three cited numbers. Plan: `set_cache_limit(offer-cache, 16384)`, risk low. It paused. `pz approve <id>` in a new terminal resumed the graph from its SQLite checkpoint, executed through a separate actions-enabled tool session, and verify re-queried p99: 591 → 120 ms against a 118 ms baseline → resolved. Eight tool calls, about a minute, on a free Gemini flash-lite model.

**Q: What was the hardest part?**
Making it genuinely provider-agnostic. Each free provider broke a different assumption: Groq rejects empty-list tool results and has an 8k tokens/minute cap; Gemini returns message content as blocks instead of strings and its newest model takes 90 s per turn; schema validators differ on open dict fields. The fix each time was to push the invariant into my own layer (wrap lists, normalise content, avoid dict schemas, measure a tool-calling turn before choosing a model) rather than special-case a provider.

**Q: Why is verification code and not the LLM?**
Because an LLM would happily declare victory. Verify re-queries the alert's key metric and checks it numerically against the pre-incident baseline and the peak. In the synthetic world a wrong fix leaves the metric where it was, so a wrong plan shows up as "not recovered" — the agent can't talk its way past it.

**Q: Is the live backend real or a stub?**
Real but thin. Prometheus (HTTP API), Kafka (admin offsets vs end offsets), Redis (`INFO`), GitHub (commits + diffs) and Loki are genuine readers behind the same Protocols as the synthetic world, and I smoke-tested them against Prometheus/Redis/Redpanda in Docker — e.g. produced 10 messages, committed 4, and the adapter reported lag 6 across two partitions. Deploy and flag history are file-backed plug points rather than integrations with one specific CI or flag service, and the live actuator is dry-run by default: it logs what it would do and changes nothing unless you opt in. I'd rather ship a safe default than a surprising one.

**Q: What would you do next?**
Web approval UI; real alert ingestion (Alertmanager webhook is already in the API); more scenario families; a larger bench with cheaper models.

**Q: Isn't the demo rigged — you pick "cache-eviction" and the agent finds cache-eviction?**
The agent never sees the ground truth; it only gets the alert and the read tools — the benchmark misses prove it can be wrong. But a dropdown *looks* rigged, so the default demo is **blind**: the server picks the failure family and seed, hides both from me and the agent, and only after the incident is over does it reveal the planted cause next to the agent's diagnosis with ✔/✘ on root cause, culprit and fix. Same scorer as the benchmark. Lesson I took from it: a demo has to be *falsifiable* in front of the viewer, not just correct.

**Q: Did it ever run against something real, or only your generated incidents?**
Yes. `demo/` is a small real system in Docker — a FastAPI service on Postgres and Redis, a Kafka consumer group, a load generator, Prometheus and Alertmanager. `pz demo chaos bad-deploy` really redeploys the service with an N+1 query; Prometheus alerts; Alertmanager pages Pager-Zero; the agent investigates through the **same live adapters** it would use on production (Prometheus HTTP API, Kafka admin, Redis INFO, a deploy log); on approval it really rolls the container back, and verification watches the real p99 drop. Measured: 24 → 495 ms, diagnosed in 40 s, p99 back to 23.7 ms 60 s after approval.

**Q: What broke when you went from synthetic to real?**
Three things, all instructive: (1) the MCP subprocess got a stripped environment and silently served the synthetic world — "works on defaults" is the worst failure mode; (2) the live server pinned `now` at start-up, so verify was reading a frozen snapshot and reported "not recovered" while Prometheus showed recovery — the synthetic world *needs* a frozen clock and the live one must never have one; (3) free-tier quotas (requests/day, tokens/day) — so I added a local Ollama model as a zero-quota provider with a two-line config change.

**Q: Give me a case where the agent was wrong and the system caught it.**
Sealed random fault on the real stack turned out to be consumer-lag (group scaled to 1 slow member). The agent diagnosed consumer-lag correctly and proposed `scale_consumers(members=2)` — the right *action*, but 2 members at 150 ms/message drain 13 msg/s against 20 msg/s incoming. The scale-out really ran; the real lag kept climbing; verification (code, reading Kafka lag from Prometheus for 5 min) returned **not recovered** and the incident closed *unresolved*. The reveal card showed ✔ family, ✔ action, ✘ recovery. That's the whole design in one incident: the model proposes, the human approves, the system decides.

