# 03 · Decisions and the "why" (plain English)

Short list of the choices someone will ask about. Full locked list: `../session/DECISIONS.md`.

**Model-agnostic from the start.** The agent reaches the LLM only through LangChain's `init_chat_model`; provider and model are two `.env` lines. I deliberately used only features every provider has (tool calling, text, Pydantic structured output). Reason: I don't want the project tied to one vendor's key, and I wanted a benchmark row for a free-tier model.

**Develop on the strongest model, benchmark on two.** The strongest model surfaces *my* bugs fastest; then the same bench runs on a free model. Both numbers are reported.

**CLI first, web UI later.** The approval interaction is the core UX; a CLI proves it without me spending time on front-end. Web UI is a planned phase 2.

**Synthetic incidents are the primary path.** I can't demo on a live outage. A generator gives me labelled, reproducible incidents (`--seed`) across six failure families. The real-infra Docker playground exists only to prove adapters work on live APIs.

**Six scenario families, inspired by real on-call experience, written generically.** Bad deploy, feature-flag regression, Kafka consumer lag, cache eviction storm, hot partition, downstream outage. Fictional service names, open-source tech (Scylla/Redis/Kafka). Nothing proprietary from any employer.

**Hard safety line.** Tools are split read-only vs mutating. Mutating tools cannot be invoked before the LangGraph `interrupt()` that a human resolves with `pz approve`. Step cap (default 12) lives in graph state.

**Local embeddings.** `all-MiniLM-L6-v2` via sentence-transformers — pgvector memory needs no API key.

**Resume numbers are measured, not promised.** 92% / 85% / 20 were my draft targets; the README reports what the bench actually produced.
