"""Incident memory (notes Phase 4).

Resolved incidents and runbooks are embedded (local MiniLM, no API key) and stored in Postgres +
pgvector. At the start of a new incident the `recall` node retrieves the most similar ones as
hypotheses — never as proof.

Backends (MEMORY_BACKEND): `pgvector` (real), `memory` (in-process, for tests / no Docker),
`off`. Every backend degrades gracefully: a failing store never breaks an investigation.
"""

from __future__ import annotations

from functools import lru_cache

from pager_zero.memory.store import MemoryStore, NullStore


@lru_cache(maxsize=1)
def get_store() -> MemoryStore:
    from pager_zero.config import settings

    if settings.memory_backend == "off":
        return NullStore()
    if settings.memory_backend == "memory":
        from pager_zero.memory.store import InMemoryStore

        return InMemoryStore()
    from pager_zero.memory.store import PgVectorStore

    return PgVectorStore(settings.database_url)


__all__ = ["get_store", "MemoryStore", "NullStore"]
