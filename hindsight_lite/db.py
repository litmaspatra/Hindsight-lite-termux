from __future__ import annotations

import heapq
import json
import operator
import os
import re
import sqlite3
import sys
import unicodedata
import uuid
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .embeddings import pack_vector, unpack_vector, validate_vector
from .schema import MIGRATIONS, SCHEMA_VERSION
from .textmatch import keywords, tokenize

_ALLOWED_MEMORY_TYPES = {"world", "experience", "observation"}
_ALLOWED_STATUS = {"active", "superseded", "deleted"}
_SAFE_PREDICATE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_MEMORY_CHARS = 120_000
MAX_SOURCE_CHARS = 512
MAX_SUBTYPE_CHARS = 80
MAX_ENTITY_NAME_CHARS = 160
MAX_ENTITY_TYPE_CHARS = 80
MAX_ENTITY_ALIASES = 64
MAX_ROLE_CHARS = 80


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    return " ".join(text.split())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _safe_fts_query(query: str) -> str:
    """Convert free text into a conservative FTS5 OR query over content words.

    Terms are quoted (no FTS operator injection); longer terms use prefix
    matching so "themes"/"theme" and "likes"/"like" still meet. bm25 ranks
    memories matching more terms first.
    """
    terms = keywords(query)
    if not terms:
        return ""
    parts = []
    for term in terms:
        quoted = '"' + term.replace('"', '""') + '"'
        parts.append(quoted + ("*" if len(term) >= 4 else ""))
    return " OR ".join(parts)


class _Conn(sqlite3.Connection):
    """`with store._connect() as conn` commits/rolls back AND closes (the stdlib
    context manager leaves the handle open, which leaks fds on Android)."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


class MemoryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._gen = 0  # bumped on every committed write; part of the vector-cache key
        self._vec_cache: dict[tuple, tuple[tuple, list]] = {}
        self._migrate()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_Conn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def transaction(self, *, bump: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
            if bump:
                self._gen += 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._connect() as conn:
            # Migration 1 creates schema_migrations; later migrations are then
            # applied strictly in order and recorded transactionally.
            conn.executescript(MIGRATIONS[0][1])
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (MIGRATIONS[0][0],),
            )
            current = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {current} is newer than supported version {SCHEMA_VERSION}"
                )
            for version, script in MIGRATIONS[1:]:
                if version <= current:
                    continue
                conn.executescript(script)
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (version,),
                )
                current = version
            conn.commit()

    def create_memory(
        self,
        content: str,
        *,
        memory_type: str,
        subtype: Optional[str] = None,
        source: Optional[str] = None,
        confidence: float = 0.5,
        importance: float = 0.5,
        event_time: Optional[str] = None,
    ) -> str:
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        if len(content) > MAX_MEMORY_CHARS:
            raise ValueError("content exceeds size limit")
        if subtype is not None and len(str(subtype)) > MAX_SUBTYPE_CHARS:
            raise ValueError("subtype exceeds size limit")
        if source is not None and len(str(source)) > MAX_SOURCE_CHARS:
            raise ValueError("source exceeds size limit")
        if memory_type not in _ALLOWED_MEMORY_TYPES:
            raise ValueError("invalid memory_type")
        if not (0 <= confidence <= 1 and 0 <= importance <= 1):
            raise ValueError("confidence and importance must be between 0 and 1")
        memory_id = _new_id("mem")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO memories(
                    id, content, normalized_content, memory_type, subtype,
                    source, confidence, importance, event_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    content,
                    normalize_text(content),
                    memory_type,
                    subtype,
                    source,
                    confidence,
                    importance,
                    event_time,
                ),
            )
        return memory_id

    def get_memory(self, memory_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return dict(row) if row else None

    def update_memory(self, memory_id: str, **changes) -> bool:
        allowed = {"content", "memory_type", "subtype", "source", "confidence", "importance", "event_time", "status"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported fields: {sorted(unknown)}")
        if not changes:
            return False
        if "memory_type" in changes and changes["memory_type"] not in _ALLOWED_MEMORY_TYPES:
            raise ValueError("invalid memory_type")
        if "status" in changes and changes["status"] not in _ALLOWED_STATUS:
            raise ValueError("invalid status")
        for key in ("confidence", "importance"):
            if key in changes and not (0 <= float(changes[key]) <= 1):
                raise ValueError(f"{key} must be between 0 and 1")
        if "subtype" in changes and changes["subtype"] is not None and len(str(changes["subtype"])) > MAX_SUBTYPE_CHARS:
            raise ValueError("subtype exceeds size limit")
        if "source" in changes and changes["source"] is not None and len(str(changes["source"])) > MAX_SOURCE_CHARS:
            raise ValueError("source exceeds size limit")
        if "content" in changes:
            content = str(changes["content"]).strip()
            if not content:
                raise ValueError("content must not be empty")
            if len(content) > MAX_MEMORY_CHARS:
                raise ValueError("content exceeds size limit")
            changes["content"] = content
            changes["normalized_content"] = normalize_text(content)
            allowed = allowed | {"normalized_content"}

        fields = list(changes)
        assignments = ", ".join(f"{field} = ?" for field in fields)
        values = [changes[field] for field in fields]
        with self.transaction() as conn:
            cur = conn.execute(
                f"UPDATE memories SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # fields are allowlisted
                (*values, memory_id),
            )
            return cur.rowcount > 0

    def soft_delete_memory(self, memory_id: str) -> bool:
        return self.update_memory(memory_id, status="deleted")

    def purge_memory(self, memory_id: str) -> bool:
        """Hard delete: row, FTS entry, embedding and links are removed."""
        with self.transaction() as conn:
            return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def touch_recalled(self, memory_ids: Iterable[str]) -> None:
        """Record that memories were surfaced (drives 'no longer useful' expiry).
        Not an edit: updated_at, FTS and the vector cache are left alone."""
        ids = list(dict.fromkeys(str(m) for m in memory_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.transaction(bump=False) as conn:
            conn.execute(
                f"""UPDATE memories SET recall_count = recall_count + 1,
                    last_recalled_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})""",
                ids,
            )

    def supersede_memory(self, old_memory_id: str, new_memory_id: str) -> None:
        if old_memory_id == new_memory_id:
            raise ValueError("memory cannot supersede itself")
        with self.transaction() as conn:
            new = conn.execute("SELECT id FROM memories WHERE id = ?", (new_memory_id,)).fetchone()
            old = conn.execute("SELECT id FROM memories WHERE id = ?", (old_memory_id,)).fetchone()
            if not old or not new:
                raise KeyError("both memories must exist")
            conn.execute(
                "UPDATE memories SET status='superseded', superseded_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_memory_id, old_memory_id),
            )

    def search_fts(self, query: str, *, limit: int = 10, include_inactive: bool = False) -> list[dict]:
        safe_query = _safe_fts_query(query)
        if not safe_query:
            return []
        limit = max(1, min(int(limit), 100))
        status_clause = "" if include_inactive else "AND m.status = 'active'"
        sql = f"""
            SELECT m.*, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memories m ON m.rowid = memory_fts.rowid
            WHERE memory_fts MATCH ? {status_clause}
            ORDER BY rank ASC
            LIMIT ?
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (safe_query, limit)).fetchall()]

    def create_entity(self, canonical_name: str, *, entity_type: Optional[str] = None, aliases: Iterable[str] = ()) -> str:
        canonical_name = canonical_name.strip()
        if not canonical_name:
            raise ValueError("canonical_name must not be empty")
        if len(canonical_name) > MAX_ENTITY_NAME_CHARS:
            raise ValueError("canonical_name exceeds size limit")
        if entity_type is not None and len(str(entity_type)) > MAX_ENTITY_TYPE_CHARS:
            raise ValueError("entity_type exceeds size limit")
        clean_aliases = sorted({str(a).strip() for a in aliases if str(a).strip()})
        if len(clean_aliases) > MAX_ENTITY_ALIASES or any(len(a) > MAX_ENTITY_NAME_CHARS for a in clean_aliases):
            raise ValueError("aliases exceed size limits")
        entity_id = _new_id("ent")
        aliases_json = json.dumps(clean_aliases, ensure_ascii=False)
        with self.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO entities(id, canonical_name, entity_type, aliases_json) VALUES (?, ?, ?, ?)",
                    (entity_id, canonical_name, entity_type, aliases_json),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id FROM entities WHERE canonical_name = ? AND entity_type IS ?",
                    (canonical_name, entity_type),
                ).fetchone()
                if row:
                    return row["id"]
                raise
        return entity_id

    def get_entity(self, entity_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["aliases"] = json.loads(out.pop("aliases_json"))
            return out

    def link_memory_entity(self, memory_id: str, entity_id: str, *, role: str = "mentioned") -> None:
        role = role.strip() or "mentioned"
        if len(role) > MAX_ROLE_CHARS:
            raise ValueError("role exceeds size limit")
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_entities(memory_id, entity_id, role) VALUES (?, ?, ?)",
                (memory_id, entity_id, role),
            )

    def create_relationship(
        self,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        *,
        confidence: float = 0.5,
    ) -> str:
        predicate = predicate.strip()
        if not _SAFE_PREDICATE.fullmatch(predicate):
            raise ValueError("predicate contains unsafe characters")
        if not (0 <= confidence <= 1):
            raise ValueError("confidence must be between 0 and 1")
        relationship_id = _new_id("rel")
        with self.transaction() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO relationships(id, subject_entity_id, predicate, object_entity_id, confidence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (relationship_id, subject_entity_id, predicate, object_entity_id, confidence),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """SELECT id FROM relationships
                       WHERE subject_entity_id=? AND predicate=? AND object_entity_id=?""",
                    (subject_entity_id, predicate, object_entity_id),
                ).fetchone()
                if row:
                    return row["id"]
                raise
        return relationship_id

    def link_memory_relationship(self, memory_id: str, relationship_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_relationships(memory_id, relationship_id) VALUES (?, ?)",
                (memory_id, relationship_id),
            )

    def neighbors(self, entity_id: str, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name
                FROM relationships r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE r.subject_entity_id = ? OR r.object_entity_id = ?
                ORDER BY r.updated_at DESC
                LIMIT ?
                """,
                (entity_id, entity_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]



    def find_active_by_normalized_content(self, content: str) -> Optional[dict]:
        normalized = normalize_text(content)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE normalized_content = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (normalized,),
            ).fetchone()
            return dict(row) if row else None

    def match_entity_ids(self, query: str, *, limit: int = 25) -> list[tuple[str, int]]:
        """Entities whose name/alias words appear in the query, best first.

        Whole-word matching only ("term" does not match "Termux"). A full
        multi-word name found inside the query scores higher than a single
        shared word.
        """
        terms = set(keywords(query, limit=12))
        if not terms:
            return []
        padded = " " + " ".join(tokenize(query)) + " "
        clauses = " OR ".join("lower(canonical_name) LIKE ? OR lower(aliases_json) LIKE ?" for _ in terms)
        params: list[str] = []
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, canonical_name, aliases_json FROM entities WHERE {clauses} LIMIT 500", params
            ).fetchall()
        scored: list[tuple[str, int]] = []
        for row in rows:
            try:
                aliases = json.loads(row["aliases_json"])
            except (TypeError, ValueError):
                aliases = []
            best = 0
            for name in (row["canonical_name"], *aliases):
                words = tokenize(name)
                if not words:
                    continue
                if " " + " ".join(words) + " " in padded:
                    best = max(best, 10 + len(words))
                else:
                    shared = sum(1 for w in words if w in terms)
                    best = max(best, shared)
            if best:
                scored.append((row["id"], best))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def find_entity_by_name(self, name: str) -> Optional[dict]:
        target = normalize_text(name)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM entities").fetchall()
            for row in rows:
                aliases = json.loads(row["aliases_json"])
                if normalize_text(row["canonical_name"]) == target or any(normalize_text(a) == target for a in aliases):
                    out = dict(row)
                    out["aliases"] = aliases
                    return out
        return None

    def find_or_create_entity(self, canonical_name: str, *, entity_type: Optional[str] = None, aliases: Iterable[str] = ()) -> str:
        existing = self.find_entity_by_name(canonical_name)
        if existing:
            return existing["id"]
        return self.create_entity(canonical_name, entity_type=entity_type, aliases=aliases)

    def entity_names_for_memory(self, memory_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.canonical_name
                FROM memory_entities me
                JOIN entities e ON e.id = me.entity_id
                WHERE me.memory_id = ?
                ORDER BY e.canonical_name
                """,
                (memory_id,),
            ).fetchall()
            return [row["canonical_name"] for row in rows]

    def active_memories_for_entity_names(self, names: Iterable[str], *, subtype: Optional[str] = None, limit: int = 50) -> list[dict]:
        normalized = {normalize_text(n) for n in names if str(n).strip()}
        if not normalized:
            return []
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            entities = conn.execute("SELECT id, canonical_name, aliases_json FROM entities").fetchall()
            ids = []
            for row in entities:
                aliases = json.loads(row["aliases_json"])
                values = {normalize_text(row["canonical_name"]), *(normalize_text(a) for a in aliases)}
                if normalized & values:
                    ids.append(row["id"])
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            sql = f"""
                SELECT DISTINCT m.*
                FROM memories m
                JOIN memory_entities me ON me.memory_id = m.id
                WHERE me.entity_id IN ({placeholders})
                  AND m.status = 'active'
                  AND (? IS NULL OR m.subtype = ?)
                ORDER BY m.updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (*ids, subtype, subtype, limit)).fetchall()
            return [dict(r) for r in rows]

    def active_memories_for_relationship_signature(self, subject_name: str, predicate: str) -> list[dict]:
        subject = self.find_entity_by_name(subject_name)
        if not subject:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT m.*, o.canonical_name AS relationship_object
                FROM relationships r
                JOIN entities o ON o.id = r.object_entity_id
                JOIN memory_relationships mr ON mr.relationship_id = r.id
                JOIN memories m ON m.id = mr.memory_id
                WHERE r.subject_entity_id = ? AND r.predicate = ? AND m.status = 'active'
                ORDER BY m.updated_at DESC
                """,
                (subject["id"], predicate),
            ).fetchall()
            return [dict(r) for r in rows]

    def _cache_signature(self) -> tuple:
        """Changes when embeddings or memory status change (here or in another
        process). Deliberately ignores recall bookkeeping so reads stay cached."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT (SELECT COUNT(*) FROM memory_embeddings),
                       (SELECT COALESCE(MAX(updated_at), '') FROM memory_embeddings),
                       (SELECT COUNT(*) FROM memories WHERE status = 'active'),
                       (SELECT COALESCE(MAX(updated_at), '') FROM memories WHERE status != 'active')
                """
            ).fetchone()
        return (self._gen, *tuple(row))

    def _load_vectors(self, provider: str, model: str, dimension: int) -> list[tuple[str, array]]:
        """Active memories' unit vectors as compact float32 arrays (4 bytes/float)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT me.memory_id, me.vector, me.norm
                FROM memory_embeddings me JOIN memories m ON m.id = me.memory_id
                WHERE me.provider = ? AND me.model = ? AND me.dimension = ? AND m.status = 'active'
                """,
                (provider, model, dimension),
            ).fetchall()
        out: list[tuple[str, array]] = []
        for row in rows:
            payload = row["vector"]
            norm = float(row["norm"])
            if len(payload) != dimension * 4 or norm <= 0:
                continue
            vec = array("f")
            vec.frombytes(payload)
            if sys.byteorder == "big":
                vec.byteswap()
            out.append((row["memory_id"], array("f", (x / norm for x in vec))))
        return out

    def top_semantic(
        self, vector, *, provider: str, model: str, limit: int = 20, min_score: float = -1.0
    ) -> list[dict]:
        """Cosine top-k over active memories. Vectors are cached between calls and
        the cache is invalidated by any write (in this or another process)."""
        clean, qnorm = validate_vector(vector)
        limit = max(1, min(int(limit), 200))
        key = (provider, model, len(clean))
        sig = self._cache_signature()
        cached = self._vec_cache.get(key)
        if cached is None or cached[0] != sig:
            cached = (sig, self._load_vectors(provider, model, len(clean)))
            self._vec_cache[key] = cached
        query = array("f", (x / qnorm for x in clean))
        mul = operator.mul
        scored = heapq.nlargest(
            limit,
            ((sum(map(mul, query, vec)), mid) for mid, vec in cached[1]),
            key=lambda item: (item[0], item[1]),
        )
        scored = [(sc, mid) for sc, mid in scored if sc >= float(min_score)]
        if not scored:
            return []
        placeholders = ",".join("?" for _ in scored)
        with self._connect() as conn:
            rows = {
                r["id"]: dict(r)
                for r in conn.execute(
                    f"SELECT * FROM memories WHERE status = 'active' AND id IN ({placeholders})",
                    [mid for _, mid in scored],
                ).fetchall()
            }
        out = []
        for sc, mid in scored:
            if mid in rows:
                row = rows[mid]
                row["semantic_score"] = sc
                out.append(row)
        out.sort(key=lambda r: (-r["semantic_score"], -float(r["importance"]), -float(r["confidence"]), r["id"]))
        return out

    def best_semantic_match(self, vector, *, provider: str, model: str) -> Optional[dict]:
        rows = self.top_semantic(vector, provider=provider, model=model, limit=1)
        if not rows:
            return None
        best = dict(rows[0])
        best["similarity"] = best["semantic_score"]
        return best

    def set_embedding(
        self,
        memory_id: str,
        vector,
        *,
        provider: str,
        model: str,
    ) -> None:
        provider = str(provider).strip()
        model = str(model).strip()
        if not provider or not model:
            raise ValueError("provider and model must not be empty")
        clean, norm = validate_vector(vector)
        payload = pack_vector(clean)
        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if not exists:
                raise KeyError("memory does not exist")
            conn.execute(
                """
                INSERT INTO memory_embeddings(
                    memory_id, provider, model, dimension, vector, norm
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    provider=excluded.provider,
                    model=excluded.model,
                    dimension=excluded.dimension,
                    vector=excluded.vector,
                    norm=excluded.norm,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (memory_id, provider, model, len(clean), payload, norm),
            )

    def get_embedding(self, memory_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return None
            out = dict(row)
            out["vector"] = unpack_vector(out["vector"], int(out["dimension"]))
            return out

    def iter_embeddings(
        self,
        *,
        provider: str,
        model: str,
        dimension: int,
        active_only: bool = True,
    ):
        if dimension < 1 or dimension > 16384:
            raise ValueError("invalid embedding dimension")
        status_clause = "AND m.status = 'active'" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT me.memory_id, me.dimension, me.vector, me.norm, m.*
                FROM memory_embeddings me
                JOIN memories m ON m.id = me.memory_id
                WHERE me.provider = ? AND me.model = ? AND me.dimension = ?
                {status_clause}
                """,
                (provider, model, dimension),
            ).fetchall()
            for row in rows:
                out = dict(row)
                out["vector"] = unpack_vector(out["vector"], int(out["dimension"]))
                yield out

    def delete_embedding(self, memory_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
            )
            return cur.rowcount > 0

    def record_retention_event(self, *, action: str, memory_id: str | None = None, superseded_id: str | None = None, reason: str = "", source: str | None = None) -> None:
        if action not in {"created", "updated", "superseded", "ignored"}:
            raise ValueError("invalid retention action")
        reason = str(reason)[:240]
        source = str(source)[:240] if source is not None else None
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO retention_events(action, memory_id, superseded_id, reason, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (action, memory_id, superseded_id, reason, source),
            )

    def recent_retention_events(self, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM retention_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


    def list_memories(self, *, include_inactive: bool = True, limit: int = 10000) -> list[dict]:
        limit = max(1, min(int(limit), 100000))
        clause = "" if include_inactive else "WHERE status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories {clause} ORDER BY updated_at DESC, id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_entities(self, *, limit: int = 10000) -> list[dict]:
        limit = max(1, min(int(limit), 100000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM entities ORDER BY canonical_name COLLATE NOCASE, id LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["aliases"] = json.loads(item.pop("aliases_json"))
                out.append(item)
            return out

    def memories_for_entity(self, entity_id: str, *, include_inactive: bool = False, limit: int = 500) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        status_clause = "" if include_inactive else "AND m.status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT m.*
                FROM memory_entities me
                JOIN memories m ON m.id = me.memory_id
                WHERE me.entity_id = ? {status_clause}
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (entity_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def entities_for_memory(self, memory_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.canonical_name, e.entity_type, e.aliases_json, me.role
                FROM memory_entities me
                JOIN entities e ON e.id = me.entity_id
                WHERE me.memory_id = ?
                ORDER BY e.canonical_name COLLATE NOCASE
                """,
                (memory_id,),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["aliases"] = json.loads(item.pop("aliases_json"))
                out.append(item)
            return out

    def relationships_for_memory(self, memory_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name
                FROM memory_relationships mr
                JOIN relationships r ON r.id = mr.relationship_id
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                WHERE mr.memory_id = ?
                ORDER BY r.predicate, s.canonical_name, o.canonical_name
                """,
                (memory_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _db_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.stat(str(self.db_path) + suffix).st_size
            except OSError:
                pass
        return total

    def stats(self) -> dict:
        with self._connect() as conn:
            return {
                "schema_version": conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0],
                "memories": conn.execute("SELECT count(*) FROM memories WHERE status != 'deleted'").fetchone()[0],
                "active_memories": conn.execute("SELECT count(*) FROM memories WHERE status = 'active'").fetchone()[0],
                "superseded_memories": conn.execute("SELECT count(*) FROM memories WHERE status = 'superseded'").fetchone()[0],
                "deleted_memories": conn.execute("SELECT count(*) FROM memories WHERE status = 'deleted'").fetchone()[0],
                "db_bytes": self._db_bytes(),
                "entities": conn.execute("SELECT count(*) FROM entities").fetchone()[0],
                "relationships": conn.execute("SELECT count(*) FROM relationships").fetchone()[0],
                "embeddings": conn.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0],
                "retention_events": conn.execute("SELECT count(*) FROM retention_events").fetchone()[0],
            }
