"""Seed runbooks: generic operational knowledge the agent can recall as hypotheses.
Nothing here is specific to any real company."""

from __future__ import annotations

from pager_zero.memory.store import Memory

RUNBOOKS: list[Memory] = [
    Memory(
        "runbook",
        "Latency spike right after a deploy",
        "If p99 latency deviates within minutes of a deploy, compare the deploy time with the first "
        "deviation, read the commits in that release and look for N+1 queries, shrunk pools, or new "
        "synchronous upstream calls. Rollback to the previous version is the safe first action.",
        {"family": "bad-deploy", "fix_action": "rollback_deploy"},
    ),
    Memory(
        "runbook",
        "Behaviour change with no deploy",
        "When metrics change but there is no deploy, check dynamic config / feature-flag history for the "
        "service's flags. A TTL set to 0, a disabled fallback, or a batch size of 1 explain cache-miss "
        "storms, error spikes and consumer slowdowns respectively. Revert the flag to its previous value.",
        {"family": "flag-regression", "fix_action": "set_flag"},
    ),
    Memory(
        "runbook",
        "Consumer lag growing",
        "Growing consumer lag has two usual causes: fewer members (pods OOM-killed → rebalance to 1 member) "
        "or a producer burst (bulk job). Check members, pods_ready and producer rate. Scale the group back "
        "to its previous size, or to double for a burst.",
        {"family": "consumer-lag", "fix_action": "scale_consumers"},
    ),
    Memory(
        "runbook",
        "Cache hit-rate collapse",
        "A cache at its maxmemory evicts continuously: evictions/s explodes, hit rate collapses, and the DB "
        "behind it saturates (db_qps up, pool wait up). Confirm used_mb ≈ max and a key-count growth event "
        "(catalog refresh). Raise the memory limit (about 2x) to stop the eviction storm.",
        {"family": "cache-eviction", "fix_action": "set_cache_limit"},
    ),
    Memory(
        "runbook",
        "One shard hot, others idle",
        "If a single partition/shard shows 10x+ latency while others are flat, look at hot_keys: a campaign "
        "or viral item can push one key to 40% of traffic. Rate-limit that key (≈500 rps) rather than scaling "
        "the whole cluster.",
        {"family": "hot-partition", "fix_action": "rate_limit_key"},
    ),
    Memory(
        "runbook",
        "Dependency failing, retries amplifying",
        "When a dependency's error rate jumps and the caller's retries multiply load (retries/s up, p99 to "
        "timeout x attempts), the caller must degrade gracefully: enable the fallback / circuit breaker for "
        "that dependency. Fixing the dependency itself is a separate incident.",
        {"family": "downstream-outage", "fix_action": "enable_fallback"},
    ),
]
