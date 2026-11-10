# 06 — Likely questions, crisp answers

## Opening

**Tell me about this project.**
On-call agent. Alert in → it investigates with tools like an engineer would → diagnosis with
evidence → one proposed fix → stops for human approval → executes → verifies with code that the
metric recovered. MCP tool server, LangGraph agent with a step cap and an approval interrupt,
adapters that swap between a generated world (for measurement) and real systems (Prometheus,
Kafka, Redis, GitHub). 95 % root-cause / 95 % correct-fix on 20 generated incidents; proven live
on a containerised stack with chaos-injected faults.

**Why build this?**
The first 10–30 minutes of most incidents are mechanical and tool-driven — exactly what LLM agents
are good at — but the risk of an agent *acting* on production is unacceptable. So the product
idea is "prepare, don't act": do the boring part, hand a human a one-click decision, verify.

**What's the hardest part?**
Making it trustworthy rather than impressive: bounding the loop, keeping mutating tools out of
the investigation process entirely, verifying with code instead of asking the model, and making
the demo falsifiable (blind mode / sealed chaos). The second hardest was the live backend — three
of my nastiest bugs only existed when a real system was on the other side.

## Architecture

**Why MCP and not plain function calling?**
Standard protocol: the same tool server is driven by my agent, by Claude Code, by Cursor. It also
gives me a *process boundary* for safety — the investigation session literally doesn't have the
mutating tools registered.

**Why LangGraph?**
I needed a state machine with a durable pause. `interrupt()` + a checkpointer means "approve" can
happen hours later from another process, and the graph resumes exactly where it stopped. A
hand-rolled loop would have to reinvent that.

**How is it model-agnostic?**
One factory (`init_chat_model`), and I only use capabilities every serious model has: tool
calling, text, structured output. No provider-specific features anywhere. Tested on Gemini, Groq,
Anthropic and a local Ollama model. Switching is two lines in `.env`.

**How do you stop it from doing something dangerous?**
Three layers: (1) the mutating tools aren't registered in the investigation session; (2) the
graph interrupts before execute and needs an explicit human decision; (3) the live actuator is
dry-run unless deliberately enabled. And the step cap bounds cost and runtime.

**Why a step cap of 12?**
Measured: correct diagnoses took 5–10 tool calls; 12 leaves headroom without letting a confused
model burn budget. On exhaustion it must conclude with what it has — "unknown" is a valid answer.

## The synthetic world

**Isn't the benchmark rigged if you generate the incidents?**
It's a closed-world benchmark with hidden ground truth — like every eval. The agent only sees the
alert and the tools; the planted truth lives in a field it has no tool for. Within a family,
every instance differs (service, numbers, timing, culprit, red herrings) and is seeded, so runs
are reproducible. What *is* pre-known is the taxonomy (6 families, 7 actions) — I say that
plainly, and it's why I also built the live demo.

**How realistic are the generated incidents?**
Internally consistent (everything follows from one cause time; unrelated signals stay flat) and
decorated with red herrings (an unrelated deploy hours earlier, an old flag flip, a harmless
sibling commit). Cleaner than real life: one cause per incident, no overlapping faults, no flapping
alerts. A "harder bench" with compound and out-of-catalogue incidents is the natural next step.

**What's blind mode?**
The server picks family + seed and hides both from me and the agent until the incident is over,
then reveals the planted truth next to the agent's answer with ✔/✘, scored by the same code as the
benchmark. A demo has to be falsifiable in front of the viewer.

## The live system

**Did it ever run on something real?**
Yes — a small real stack in Docker: FastAPI service on Postgres + Redis, Kafka consumer group,
load generator, Prometheus, Alertmanager. `pz demo chaos bad-deploy` really restarts the service
with slow code; Prometheus alerts; Alertmanager pages the agent; it diagnoses the deploy; I
approve; it really rolls the container back; the real p99 drops from 495 ms to 24 ms and
verification says recovered. Nothing in the agent knows what I broke.

**Give me a case where it was wrong.**
Sealed random fault: consumer group cut to one slow member. Agent: consumer-lag ✔, proposed
scaling to 2 — right action, too small (2 × 150 ms = 13 msg/s vs 20 in). The scale-out really ran,
the real lag kept climbing, verify said not recovered, incident closed unresolved. Exactly the
designed behaviour: the model proposes, the human approves, the system decides.

**How would you plug it into our infra?**
Set `ADAPTER_BACKEND=live`, point `PROMETHEUS_URL`/`KAFKA_BOOTSTRAP`/`REDIS_URL`/`GITHUB_TOKEN`,
write a topology file, produce the expected metric names with recording rules, point Alertmanager
at the webhook. Actuator stays dry-run until you deliberately implement yours. Deploy and flag
history are file plug points — your CI appends to them, or you implement the two small Protocols
against your real systems.

## Measurement and quality

**Walk me through the numbers.**
20 incidents, round-robin over 6 families, seed 7, memory off, auto-approved. Scored
deterministically: family match, culprit named in the diagnosis text, action + args match the
ground truth (≥ for sizes), and the verify step's own verdict. 19/20 on each. The three misses:
one diagnosis didn't name the consumer group; one labelled a flag-caused miss storm as
cache-eviction (taxonomy ambiguity — fixed in the prompt); one proposed `value="auto"` because my
catalogue leaked an internal type tag (my bug — fixed). I kept the run-1 numbers as recorded.

**Why memory off for the benchmark?**
To measure reasoning, not lookup. With memory on, a resolved incident teaches the next similar
one; that's a feature in production and a confound in a benchmark.

**How do you test an LLM agent?**
A scripted LLM drives the whole graph in tests (approve path, reject path, step cap, checkpoint
round-trip); tools are tested in-process; adapters against mocked HTTP plus one real smoke;
demo-stack control with `docker compose` faked. 81 tests in CI with no keys and no Docker. The
benchmark is the integration test with numbers.

## Honest / tricky

**What are its limitations?**
Fixed taxonomy and fix catalogue; one alert → one fix, no re-plan after a failed verify; synthetic
benchmark is small and clean; the demo system is tiny; live adapters are thin (hot keys need a
datastore exporter; deploy/flag history are file plug points). All written in the README.

**Would you let this run unattended in production?**
No — and it's designed so it can't. Read-only investigation can run unattended (that alone saves
the first ten minutes); execution always needs a human. The next safety step would be an allow-list
of actions per service and a blast-radius limit.

**What did you learn about free-tier LLMs?**
They're counted in requests per day (Gemini: 500/model) or tokens per day (Groq: 200k). One
investigation is 7–15 requests. Budget it like money, keep a second provider, and have a zero-quota
fallback — a local 14B model works, slower and a bit weaker.

**What would you do next?**
Re-plan loop after failed verification; harder benchmark (compound faults, decoy alerts,
out-of-catalogue causes that must end as `unknown`); a stronger-model and a local-model benchmark
row; per-service action allow-lists; an "incident brief" the agent posts to the channel while it
investigates.

**What was your role / what did you use AI for?**
I designed the product constraints (prepare-don't-act, bounded, verifiable, model-agnostic,
falsifiable demo), decided the architecture and the scope at each phase, and ran and debugged
every end-to-end test; I used an AI coding assistant heavily for implementation and documentation.
I can explain every design choice and every bug in this folder because I made the calls and hit
the bugs.
