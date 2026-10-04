from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .db import MemoryStore
from .embeddings import EmbeddingBackend, EmbeddingError, validate_vector
from .textmatch import keywords, prefix_overlap


@dataclass(frozen=True)
class RetrievalHit:
    memory_id: str
    score: float
    sources: tuple[str, ...]
    content: str
    memory_type: str
    subtype: Optional[str]
    event_time: Optional[str]
    created_at: str
    updated_at: str
    confidence: float
    importance: float


@dataclass(frozen=True)
class ContextRecall:
    hits: list[RetrievalHit]
    arms: dict[str, int]
    semantic_error: Optional[str] = None


class RetrievalEngine:
    """Multi-arm retrieval engine with optional semantic vector recall.

    Lexical, entity, graph, temporal and semantic rankings are fused with
    Reciprocal Rank Fusion (RRF). Vector generation is delegated to a pluggable
    embedding backend; vectors remain stored locally in SQLite.
    """

    def __init__(self, store: MemoryStore, *, rrf_k: int = 60, embedding_backend: EmbeddingBackend | None = None):
        if rrf_k < 1:
            raise ValueError("rrf_k must be >= 1")
        self.store = store
        self.rrf_k = int(rrf_k)
        self.embedding_backend = embedding_backend

    def lexical(self, query: str, *, limit: int = 20) -> list[dict]:
        return self.store.search_fts(query, limit=limit)

    def entity(self, query: str, *, limit: int = 20) -> list[dict]:
        """Active memories linked to entities named (as whole words) in the query."""
        matched = self.store.match_entity_ids(query)
        if not matched:
            return []
        limit = max(1, min(int(limit), 100))
        ids = [eid for eid, _ in matched]
        weight = {eid: score for eid, score in matched}
        placeholders = ",".join("?" for _ in ids)
        with self.store._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.*, me.entity_id AS matched_entity_id
                FROM memory_entities me
                JOIN memories m ON m.id = me.memory_id
                WHERE m.status = 'active' AND me.entity_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        best: dict[str, dict] = {}
        for row in rows:
            d = dict(row)
            cur = best.get(d["id"])
            if cur is None or weight[d["matched_entity_id"]] > weight[cur["matched_entity_id"]]:
                best[d["id"]] = d
        ordered = sorted(
            best.values(),
            key=lambda d: (-weight[d["matched_entity_id"]], -float(d["importance"]), d["updated_at"], d["id"]),
        )
        return ordered[:limit]

    def graph(self, query: str, *, limit: int = 20, max_hops: int = 1) -> list[dict]:
        """Expand from query-matched entities through the relationship graph.

        Expansion is capped at 2 hops to avoid graph explosions on mobile.
        """
        if max_hops not in (1, 2):
            raise ValueError("max_hops must be 1 or 2")
        seeds = self.store.match_entity_ids(query)
        if not seeds:
            return []
        limit = max(1, min(int(limit), 100))
        with self.store._connect() as conn:
            frontier = {eid for eid, _ in seeds}
            visited = set(frontier)
            for _ in range(max_hops):
                placeholders = ",".join("?" for _ in frontier)
                rels = conn.execute(
                    f"""
                    SELECT subject_entity_id, object_entity_id FROM relationships
                    WHERE subject_entity_id IN ({placeholders}) OR object_entity_id IN ({placeholders})
                    """,
                    (*frontier, *frontier),
                ).fetchall()
                nxt = set()
                for rel in rels:
                    nxt.add(rel["subject_entity_id"])
                    nxt.add(rel["object_entity_id"])
                nxt -= visited
                if not nxt:
                    break
                visited |= nxt
                frontier = nxt
            placeholders = ",".join("?" for _ in visited)
            rows = conn.execute(
                f"""
                SELECT DISTINCT m.*
                FROM memories m
                LEFT JOIN memory_entities me ON me.memory_id = m.id
                LEFT JOIN memory_relationships mr ON mr.memory_id = m.id
                LEFT JOIN relationships r ON r.id = mr.relationship_id
                WHERE m.status = 'active'
                  AND (me.entity_id IN ({placeholders})
                       OR r.subject_entity_id IN ({placeholders})
                       OR r.object_entity_id IN ({placeholders}))
                ORDER BY m.importance DESC, m.updated_at DESC
                LIMIT ?
                """,
                (*visited, *visited, *visited, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def index_memory(self, memory_id: str) -> None:
        if self.embedding_backend is None:
            raise RuntimeError("no embedding backend configured")
        memory = self.store.get_memory(memory_id)
        if not memory:
            raise KeyError("memory does not exist")
        vectors = self.embedding_backend.embed([memory["content"]])
        if len(vectors) != 1:
            raise EmbeddingError("embedding backend returned the wrong number of vectors")
        self.store.set_embedding(
            memory_id,
            vectors[0],
            provider=self.embedding_backend.provider,
            model=self.embedding_backend.model,
        )

    def index_memories(self, memory_ids: Iterable[str]) -> int:
        if self.embedding_backend is None:
            raise RuntimeError("no embedding backend configured")
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return 0
        texts: list[str] = []
        valid_ids: list[str] = []
        for memory_id in ids:
            memory = self.store.get_memory(memory_id)
            if not memory:
                raise KeyError(f"memory does not exist: {memory_id}")
            texts.append(memory["content"])
            valid_ids.append(memory_id)
        vectors = self.embedding_backend.embed(texts)
        if len(vectors) != len(valid_ids):
            raise EmbeddingError("embedding backend returned the wrong number of vectors")
        dimension: int | None = None
        for memory_id, vector in zip(valid_ids, vectors):
            clean, _ = validate_vector(vector, expected_dimension=dimension)
            dimension = len(clean)
            self.store.set_embedding(
                memory_id, clean,
                provider=self.embedding_backend.provider,
                model=self.embedding_backend.model,
            )
        return len(valid_ids)

    def semantic(self, query: str, *, limit: int = 20, min_score: float = -1.0) -> list[dict]:
        if self.embedding_backend is None:
            return []
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 100))
        vectors = self.embedding_backend.embed([query])
        if len(vectors) != 1:
            raise EmbeddingError("embedding backend returned the wrong number of vectors")
        query_vector, _ = validate_vector(vectors[0])
        return self.store.top_semantic(
            query_vector,
            provider=self.embedding_backend.provider,
            model=self.embedding_backend.model,
            limit=limit,
            min_score=min_score,
        )

    def temporal(
        self,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        recent_first: bool = True,
        limit: int = 20,
    ) -> list[dict]:
        """Retrieve active memories by event_time, falling back to created_at.

        ISO-8601 strings are accepted. SQLite's datetime() normalizes supported
        formats and keeps all comparisons parameterized.
        """
        if start is None and end is None:
            raise ValueError("start or end is required")
        limit = max(1, min(int(limit), 100))
        clauses = ["m.status = 'active'"]
        params: list[object] = []
        time_expr = "datetime(COALESCE(m.event_time, m.created_at))"
        if start is not None:
            self._validate_datetime(start)
            clauses.append(f"{time_expr} >= datetime(?)")
            params.append(start)
        if end is not None:
            self._validate_datetime(end)
            clauses.append(f"{time_expr} <= datetime(?)")
            params.append(end)
        order = "DESC" if recent_first else "ASC"
        params.append(limit)
        sql = f"""
            SELECT m.*
            FROM memories m
            WHERE {' AND '.join(clauses)}
            ORDER BY {time_expr} {order}, m.importance DESC
            LIMIT ?
        """
        with self.store._connect() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def recall(
        self,
        query: str,
        *,
        limit: int = 10,
        lexical_limit: int = 20,
        entity_limit: int = 20,
        graph_limit: int = 20,
        semantic_limit: int = 20,
        temporal_start: Optional[str] = None,
        temporal_end: Optional[str] = None,
    ) -> list[RetrievalHit]:
        """Fuse available retrieval arms using RRF plus small durable priors."""
        limit = max(1, min(int(limit), 100))
        arms: list[tuple[str, list[dict]]] = [
            ("lexical", self.lexical(query, limit=lexical_limit)),
            ("entity", self.entity(query, limit=entity_limit)),
            ("graph", self.graph(query, limit=graph_limit)),
        ]
        if self.embedding_backend is not None:
            arms.append(("semantic", self.semantic(query, limit=semantic_limit)))
        if temporal_start is not None or temporal_end is not None:
            arms.append(
                (
                    "temporal",
                    self.temporal(start=temporal_start, end=temporal_end, limit=max(limit, 20)),
                )
            )

        return self._fuse(arms, limit)

    def _fuse(self, arms: list[tuple[str, list[dict]]], limit: int, *, drop_graph_only: bool = False) -> list[RetrievalHit]:
        scores: dict[str, float] = {}
        sources: dict[str, set[str]] = {}
        rows: dict[str, dict] = {}
        for source, results in arms:
            for rank, row in enumerate(results, start=1):
                mid = row["id"]
                rows[mid] = row
                scores[mid] = scores.get(mid, 0.0) + 1.0 / (self.rrf_k + rank)
                sources.setdefault(mid, set()).add(source)

        # Tiny deterministic priors only break near-ties; they cannot dominate RRF.
        for mid, row in rows.items():
            scores[mid] += float(row.get("importance", 0.5)) * 0.001
            scores[mid] += float(row.get("confidence", 0.5)) * 0.0005

        keep = [m for m in scores if not (drop_graph_only and sources[m] == {"graph"})]
        ordered = sorted(keep, key=lambda mid: (-scores[mid], mid))[:limit]
        return [
            RetrievalHit(
                memory_id=mid,
                score=scores[mid],
                sources=tuple(sorted(sources[mid])),
                content=rows[mid]["content"],
                memory_type=rows[mid]["memory_type"],
                subtype=rows[mid].get("subtype"),
                event_time=rows[mid].get("event_time"),
                created_at=rows[mid]["created_at"],
                updated_at=rows[mid]["updated_at"],
                confidence=float(rows[mid]["confidence"]),
                importance=float(rows[mid]["importance"]),
            )
            for mid in ordered
        ]

    def recall_for_context(
        self, query: str, *, limit: int = 6, min_semantic: float = 0.35, use_semantic: bool = True
    ) -> "ContextRecall":
        """Precision-first recall for automatic prompt injection.

        Unlike recall(), a memory must earn its place: it needs a real
        keyword overlap, an entity named in the query, or a semantic score of
        at least `min_semantic`. Graph neighbours only add rank to memories
        that already qualified. Returns nothing rather than padding with noise.
        """
        pool = max(10, int(limit))
        terms = keywords(query)
        need = min(2, len(terms))
        lexical = [
            r for r in self.lexical(query, limit=pool * 2)
            if need and prefix_overlap(terms, r["content"]) >= need
        ][:pool]
        arms: list[tuple[str, list[dict]]] = [
            ("lexical", lexical),
            ("entity", self.entity(query, limit=pool)),
            ("graph", self.graph(query, limit=pool, max_hops=1)),
        ]
        semantic_error = None
        if use_semantic and self.embedding_backend is not None:
            try:
                arms.append(("semantic", self.semantic(query, limit=pool, min_score=min_semantic)))
            except Exception as exc:  # network/dimension problems must never break a turn
                semantic_error = type(exc).__name__
        hits = self._fuse(arms, max(1, int(limit)), drop_graph_only=True)
        return ContextRecall(hits=hits, arms={name: len(rows) for name, rows in arms}, semantic_error=semantic_error)

    @staticmethod
    def _validate_datetime(value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("datetime value must be a non-empty string")
        candidate = value.strip().replace("Z", "+00:00")
        try:
            datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 datetime: {value}") from exc
