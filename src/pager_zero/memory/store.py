from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

log = logging.getLogger(__name__)

EMBED_DIM = 384  # all-MiniLM-L6-v2


@dataclass
class Memory:
    kind: str  # "incident" | "runbook"
    title: str
    text: str  # what gets embedded + shown to the agent
    meta: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    created: datetime = field(default_factory=lambda: datetime.now(UTC))

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "similarity": round(self.score or 0, 3),
            **{k: v for k, v in self.meta.items() if k in ("family", "fix_action")},
        }


class MemoryStore(Protocol):
    def remember(self, m: Memory) -> None: ...

    def recall(self, query: str, k: int = 3, kind: str | None = None) -> list[Memory]: ...

    def count(self) -> int: ...


# ------------------------------------------------------------------ embeddings


class Embedder:
    _model = None

    @classmethod
    def encode(cls, texts: list[str]) -> list[list[float]]:
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            from pager_zero.config import settings

            cls._model = SentenceTransformer(settings.embedding_model)
        return [list(map(float, v)) for v in cls._model.encode(texts, normalize_embeddings=True)]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # vectors are normalised


# ------------------------------------------------------------------ backends


class NullStore:
    def remember(self, m: Memory) -> None:
        return None

    def recall(self, query: str, k: int = 3, kind: str | None = None) -> list[Memory]:
        return []

    def count(self) -> int:
        return 0


class InMemoryStore:
    """Same semantics as pgvector, in a list. Used by tests and when Docker is unavailable."""

    def __init__(self) -> None:
        self._rows: list[tuple[Memory, list[float]]] = []

    def remember(self, m: Memory) -> None:
        self._rows.append((m, Embedder.encode([m.text])[0]))

    def recall(self, query: str, k: int = 3, kind: str | None = None) -> list[Memory]:
        if not self._rows:
            return []
        q = Embedder.encode([query])[0]
        scored = [(m, _cos(q, v)) for m, v in self._rows if kind is None or m.kind == kind]
        scored.sort(key=lambda t: -t[1])
        out = []
        for m, s in scored[:k]:
            mm = Memory(m.kind, m.title, m.text, m.meta, s, m.created)
            out.append(mm)
        return out

    def count(self) -> int:
        return len(self._rows)


class PgVectorStore:
    DDL = f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS memories (
        id        BIGSERIAL PRIMARY KEY,
        kind      TEXT NOT NULL,
        title     TEXT NOT NULL,
        text      TEXT NOT NULL,
        meta      JSONB NOT NULL DEFAULT '{{}}',
        embedding VECTOR({EMBED_DIM}) NOT NULL,
        created   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS memories_kind_idx ON memories (kind);
    CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories
        USING hnsw (embedding vector_cosine_ops);
    """

    def __init__(self, url: str) -> None:
        self.url = url.replace("postgresql+psycopg://", "postgresql://")
        self._ready = False

    def _conn(self):
        import psycopg
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(self.url, connect_timeout=3)
        if not self._ready:
            with conn.cursor() as cur:  # NOT `with conn`: psycopg3 closes the connection on exit
                cur.execute(self.DDL)
            conn.commit()
            self._ready = True
        register_vector(conn)
        return conn

    def remember(self, m: Memory) -> None:
        try:
            vec = Embedder.encode([m.text])[0]
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memories (kind, title, text, meta, embedding) VALUES (%s, %s, %s, %s, %s)",
                    (m.kind, m.title, m.text, json.dumps(m.meta, default=str), vec),
                )
        except Exception as e:  # noqa: BLE001 — memory must never break an incident
            log.warning("memory.remember failed: %s", e)

    def recall(self, query: str, k: int = 3, kind: str | None = None) -> list[Memory]:
        try:
            q = Embedder.encode([query])[0]
            where = "WHERE kind = %s" if kind else ""
            params: list[Any] = [q] + ([kind] if kind else []) + [k]
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT kind, title, text, meta, created, 1 - (embedding <=> %s::vector) AS sim "
                    f"FROM memories {where} ORDER BY sim DESC LIMIT %s",
                    params,
                )
                return [Memory(r[0], r[1], r[2], r[3], float(r[5]), r[4]) for r in cur.fetchall()]
        except Exception as e:  # noqa: BLE001
            log.warning("memory.recall failed (continuing without memory): %s", e)
            return []

    def count(self) -> int:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM memories")
                return int(cur.fetchone()[0])
        except Exception as e:  # noqa: BLE001
            log.warning("memory.count failed: %s", e)
            return -1


# ------------------------------------------------------------------ what gets remembered


def incident_memory(
    context: dict[str, Any],
    diagnosis: dict[str, Any],
    plan: dict[str, Any] | None,
    verification: dict[str, Any] | None,
) -> Memory:
    a = context["alert"]
    fix = f"{plan['action']}({json.dumps(plan['target'])})" if plan else "no fix"
    outcome = "recovered" if verification and verification.get("recovered") else "not verified"
    text = (
        f"{a['name']} on {a['service']}: {a['summary']}. Root cause ({diagnosis['family']}): {diagnosis['root_cause']} "
        f"Evidence: {' | '.join(diagnosis.get('evidence', [])[:3])}. Fix: {fix} → {outcome}."
    )
    return Memory(
        kind="incident",
        title=f"{a['name']} / {a['service']} / {diagnosis['family']}",
        text=text,
        meta={
            "family": diagnosis["family"],
            "service": a["service"],
            "alert": a["name"],
            "fix_action": plan["action"] if plan else None,
            "fix_target": plan["target"] if plan else None,
            "outcome": outcome,
        },
    )


def recall_query(context: dict[str, Any]) -> str:
    a = context["alert"]
    return f"{a['name']} on {a['service']}: {a['summary']}"
