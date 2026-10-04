from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .db import MemoryStore, normalize_text
from .embeddings import EmbeddingBackend, EmbeddingError
from .reconcile import Judge, clean_merged
from .safety import looks_sensitive as _looks_sensitive
from .textmatch import keywords, prefix_overlap

ALLOWED_MEMORY_TYPES = {"world", "experience", "observation"}
ALLOWED_SUBTYPES = {
    "preference", "decision", "configuration", "project_state", "procedure",
    "event", "troubleshooting", "relationship", "fact", "directive",
}
MAX_EXTRACT_INPUT_CHARS = 120_000
MAX_EXTRACT_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FACTS_PER_TURN = 32
MAX_ENTITIES_PER_FACT = 24
MAX_RELATIONSHIPS_PER_FACT = 24
_SAFE_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_SAFE_PREDICATE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
# Predicates that hold one value at a time: a new object means the old fact is stale.
_EXCLUSIVE_PREDICATES = {
    "memory_provider", "default_model", "active_model", "current_model", "current_provider",
    "primary_provider", "lives_in", "located_in", "works_at", "employer", "job_title",
    "timezone", "primary_language", "preferred_language", "phone_model",
}
_EXCLUSIVE_PREFIXES = ("current_", "default_", "primary_", "active_", "preferred_")
_STATE_SUBTYPES = {"configuration", "project_state", "decision", "preference"}


def _is_exclusive(predicate: str) -> bool:
    p = predicate.lower()
    return p in _EXCLUSIVE_PREDICATES or p.startswith(_EXCLUSIVE_PREFIXES)


class ExtractionError(RuntimeError):
    pass


class Extractor(Protocol):
    def extract(self, text: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    entity_type: str | None = None
    aliases: tuple[str, ...] = ()
    role: str = "mentioned"


@dataclass(frozen=True)
class ExtractedRelationship:
    subject: str
    predicate: str
    object: str
    confidence: float = 0.5


@dataclass(frozen=True)
class ExtractedMemory:
    content: str
    memory_type: str
    subtype: str | None = None
    confidence: float = 0.5
    importance: float = 0.5
    event_time: str | None = None
    durable: bool = True
    entities: tuple[ExtractedEntity, ...] = ()
    relationships: tuple[ExtractedRelationship, ...] = ()


@dataclass(frozen=True)
class RetentionDecision:
    action: str
    memory_id: str | None = None
    superseded_id: str | None = None
    reason: str = ""


@dataclass
class RetentionReport:
    decisions: list[RetentionDecision] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(d.action == "created" for d in self.decisions)

    @property
    def updated(self) -> int:
        return sum(d.action == "updated" for d in self.decisions)

    @property
    def superseded(self) -> int:
        return sum(d.action == "superseded" for d in self.decisions)

    @property
    def ignored(self) -> int:
        return sum(d.action == "ignored" for d in self.decisions)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class OpenAICompatibleJSONExtractor:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 45.0
    allow_insecure_remote: bool = False

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or fragments")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and not loopback and not self.allow_insecure_remote:
            raise ValueError("remote extraction endpoints must use HTTPS")
        if not self.model.strip() or len(self.model) > 256:
            raise ValueError("model must be between 1 and 256 characters")
        if self.timeout <= 0 or self.timeout > 300:
            raise ValueError("timeout must be between 0 and 300 seconds")

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def extract(self, text: str) -> dict[str, Any]:
        text = str(text).strip()
        if not text:
            return {"memories": []}
        if len(text) > MAX_EXTRACT_INPUT_CHARS:
            raise ValueError("extraction input exceeds size limit")
        system = (
            "Extract only durable long-term memory from the conversation. "
            "Return strict JSON with key 'memories'. Each item may contain: content, "
            "memory_type(world|experience|observation), subtype, confidence 0..1, "
            "importance 0..1, event_time, durable, entities[{name,entity_type,aliases,role}], "
            "relationships[{subject,predicate,object,confidence}]. "
            "Ignore transient chatter, one-off requests, secrets/credentials, and uncertain guesses. "
            "Only store what the USER said or confirmed about themselves, their world, their projects or their "
            "choices; the Assistant's lines are context, never a source of facts. "
            "State each fact as a short standalone sentence (resolve pronouns). "
            "Do not emit commands, SQL, file paths, or executable instructions."
        )
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_EXTRACT_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExtractionError(f"extraction request failed: {exc.__class__.__name__}") from exc
        if len(payload) > MAX_EXTRACT_RESPONSE_BYTES:
            raise ExtractionError("extraction response exceeded size limit")
        try:
            outer = json.loads(payload)
            content = outer["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, RecursionError) as exc:
            raise ExtractionError("extraction endpoint returned invalid structured JSON") from exc
        if not isinstance(parsed, dict):
            raise ExtractionError("extraction payload must be an object")
        return parsed


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, n))


def _validate_event_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) > 64:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return text


def _clean_name(value: Any) -> str:
    name = " ".join(str(value or "").strip().split())
    if not name or not _SAFE_NAME.fullmatch(name):
        raise ValueError("invalid entity name")
    return name


def parse_extraction(payload: dict[str, Any]) -> list[ExtractedMemory]:
    raw_memories = payload.get("memories", [])
    if not isinstance(raw_memories, list):
        raise ExtractionError("'memories' must be a list")
    out: list[ExtractedMemory] = []
    for raw in raw_memories[:MAX_FACTS_PER_TURN]:
        if not isinstance(raw, dict):
            continue
        content = " ".join(str(raw.get("content", "")).strip().split())
        if not content or len(content) > 10_000 or _looks_sensitive(content):
            continue
        memory_type = str(raw.get("memory_type", "world")).strip().lower()
        if memory_type not in ALLOWED_MEMORY_TYPES:
            continue
        subtype_raw = raw.get("subtype")
        subtype = str(subtype_raw).strip().lower() if subtype_raw is not None else None
        if subtype and subtype not in ALLOWED_SUBTYPES:
            subtype = "fact"
        entities: list[ExtractedEntity] = []
        for ent in raw.get("entities", [])[:MAX_ENTITIES_PER_FACT] if isinstance(raw.get("entities", []), list) else []:
            if not isinstance(ent, dict):
                continue
            try:
                name = _clean_name(ent.get("name"))
            except ValueError:
                continue
            aliases_raw = ent.get("aliases", [])
            aliases: list[str] = []
            if isinstance(aliases_raw, list):
                for alias in aliases_raw[:16]:
                    try:
                        cleaned = _clean_name(alias)
                    except ValueError:
                        continue
                    if normalize_text(cleaned) != normalize_text(name):
                        aliases.append(cleaned)
            role = str(ent.get("role", "mentioned")).strip()[:80] or "mentioned"
            etype = str(ent.get("entity_type", "")).strip()[:80] or None
            entities.append(ExtractedEntity(name, etype, tuple(dict.fromkeys(aliases)), role))
        relationships: list[ExtractedRelationship] = []
        rels_raw = raw.get("relationships", [])
        if isinstance(rels_raw, list):
            for rel in rels_raw[:MAX_RELATIONSHIPS_PER_FACT]:
                if not isinstance(rel, dict):
                    continue
                try:
                    subject = _clean_name(rel.get("subject"))
                    obj = _clean_name(rel.get("object"))
                except ValueError:
                    continue
                predicate = str(rel.get("predicate", "")).strip()
                if not _SAFE_PREDICATE.fullmatch(predicate):
                    continue
                relationships.append(
                    ExtractedRelationship(subject, predicate, obj, _clamp01(rel.get("confidence")))
                )
        out.append(ExtractedMemory(
            content=content,
            memory_type=memory_type,
            subtype=subtype,
            confidence=_clamp01(raw.get("confidence")),
            importance=_clamp01(raw.get("importance")),
            event_time=_validate_event_time(raw.get("event_time")),
            durable=(raw.get("durable", True) if isinstance(raw.get("durable", True), bool) else False),
            entities=tuple(entities),
            relationships=tuple(relationships),
        ))
    return out


class RetentionEngine:
    def __init__(
        self,
        store: MemoryStore,
        extractor: Extractor | None,
        *,
        embedding_backend: EmbeddingBackend | None = None,
        judge: Judge | None = None,
        keep_history: bool = True,
        semantic_duplicate_threshold: float = 0.985,
        semantic_related_threshold: float = 0.55,
        heuristic_supersede_threshold: float = 0.88,
    ):
        if not (0.0 <= semantic_related_threshold <= semantic_duplicate_threshold <= 1.0):
            raise ValueError("invalid semantic thresholds")
        self.store = store
        self.extractor = extractor
        self.embedding_backend = embedding_backend
        self.judge = judge
        self.keep_history = keep_history
        self.semantic_duplicate_threshold = semantic_duplicate_threshold
        self.semantic_related_threshold = semantic_related_threshold
        self.heuristic_supersede_threshold = heuristic_supersede_threshold

    # -- entry points ---------------------------------------------------------
    def retain(self, text: str, *, source: str | None = None) -> RetentionReport:
        if self.extractor is None:
            raise ExtractionError("no extraction model configured")
        payload = self.extractor.extract(text)
        return self._retain_items(parse_extraction(payload), source)

    def remember_direct(self, text: str, *, source: str | None = None) -> RetentionReport:
        """Store explicit user text as one memory without needing an LLM to extract it."""
        content = " ".join(str(text).split())
        if not content or len(content) > 10_000:
            raise ValueError("content must be 1..10000 characters")
        if _looks_sensitive(content):
            report = RetentionReport([RetentionDecision("ignored", reason="looks like a secret")])
            self.store.record_retention_event(action="ignored", reason="looks like a secret", source=source)
            return report
        item = ExtractedMemory(content=content, memory_type="world", subtype="fact", confidence=0.9, importance=0.7)
        return self._retain_items([item], source)

    def _retain_items(self, memories: list[ExtractedMemory], source: str | None) -> RetentionReport:
        report = RetentionReport()
        for item in memories:
            if not item.durable:
                decision = RetentionDecision("ignored", reason="not durable")
            else:
                decision = self._retain_one(item, source=source)
            report.decisions.append(decision)
            self.store.record_retention_event(
                action=decision.action,
                memory_id=decision.memory_id,
                superseded_id=decision.superseded_id,
                reason=decision.reason,
                source=source,
            )
        return report

    # -- core -----------------------------------------------------------------
    def _retain_one(self, item: ExtractedMemory, *, source: str | None) -> RetentionDecision:
        exact = self.store.find_active_by_normalized_content(item.content)
        if exact:
            return self._enrich_existing(exact, item, "exact duplicate")

        vector: list[float] | None = None
        if self.embedding_backend is not None:
            try:
                vector = self.embedding_backend.embed([item.content])[0]
            except (EmbeddingError, ValueError, OSError):
                vector = None  # retention must degrade gracefully when embeddings are unavailable
        semantic_match = self._best_match(vector)
        if semantic_match and semantic_match["similarity"] >= self.semantic_duplicate_threshold:
            return self._enrich_existing(semantic_match, item, "semantic duplicate")

        candidates = self._candidates(item, vector)
        judgement = None
        judge_note = ""
        if candidates and self.judge is not None:
            try:
                judgement = self.judge.judge(item.content, candidates)
            except Exception as exc:  # judge outage must never block or destroy anything
                judge_note = f" (judge unavailable: {type(exc).__name__})"
        allowed = {c["id"] for c in candidates}

        replaced: list[str] = []
        if judgement is not None:
            verdicts = [v for v in judgement.verdicts if v.candidate_id in allowed]
            same = next((v for v in verdicts if v.relation == "same"), None)
            if same:
                existing = self.store.get_memory(same.candidate_id)
                if existing:
                    return self._enrich_existing(existing, item, "judge: duplicate")
            replaced = [v.candidate_id for v in verdicts if v.relation == "replaces"]
            # Structured single-valued conflicts (lives_in X -> Y) are reliable; keep them too.
            replaced += [m["id"] for m in self._exclusive_conflicts(item) if m["id"] not in replaced]
        else:
            replaced = [m["id"] for m in self._heuristic_replacements(item, semantic_match)]

        content = item.content
        if replaced and judgement is not None and self.keep_history:
            # Re-validate here: whatever Judge implementation produced it, unsafe text never reaches storage.
            content = clean_merged(judgement.merged_content) or item.content

        new_id = self.store.create_memory(
            content,
            memory_type=item.memory_type,
            subtype=item.subtype,
            source=source,
            confidence=item.confidence,
            importance=item.importance,
            event_time=item.event_time,
        )
        self._store_vector(new_id, content, vector if content == item.content else None)
        self._attach_structure(new_id, item)
        for old_id in replaced:
            self.store.supersede_memory(old_id, new_id)
        if replaced:
            return RetentionDecision("superseded", new_id, superseded_id=replaced[0], reason="replaces outdated memory" + judge_note)
        return RetentionDecision("created", new_id, reason="new durable memory" + judge_note)

    # -- helpers --------------------------------------------------------------
    def _enrich_existing(self, existing: dict, item: ExtractedMemory, reason: str) -> RetentionDecision:
        """Duplicate: keep the stronger interpretation, never store the text twice."""
        changes: dict[str, Any] = {}
        if item.confidence > existing["confidence"]:
            changes["confidence"] = item.confidence
        if item.importance > existing["importance"]:
            changes["importance"] = item.importance
        if item.event_time and not existing.get("event_time"):
            changes["event_time"] = item.event_time
        if changes:
            self.store.update_memory(existing["id"], **changes)
        self._attach_structure(existing["id"], item)
        return RetentionDecision("updated" if changes else "ignored", existing["id"], reason=reason)

    def _best_match(self, vector) -> dict | None:
        if vector is None or self.embedding_backend is None:
            return None
        try:
            return self.store.best_semantic_match(
                vector, provider=self.embedding_backend.provider, model=self.embedding_backend.model
            )
        except (EmbeddingError, ValueError):
            return None

    def _store_vector(self, memory_id: str, content: str, vector: list[float] | None) -> None:
        if self.embedding_backend is None:
            return
        try:
            if vector is None:  # content differs from what was embedded (merged wording)
                vector = self.embedding_backend.embed([content])[0]
            self.store.set_embedding(
                memory_id, vector, provider=self.embedding_backend.provider, model=self.embedding_backend.model
            )
        except (EmbeddingError, ValueError, OSError):
            pass  # hmem_reindex_embeddings backfills later

    def _candidates(self, item: ExtractedMemory, vector) -> list[dict]:
        """Existing active memories that might be affected by `item`, most likely first."""
        found: dict[str, dict] = {}
        if vector is not None and self.embedding_backend is not None:
            try:
                for row in self.store.top_semantic(
                    vector, provider=self.embedding_backend.provider, model=self.embedding_backend.model,
                    limit=5, min_score=self.semantic_related_threshold,
                ):
                    found.setdefault(row["id"], row)
            except (EmbeddingError, ValueError):
                pass
        terms = keywords(item.content)
        for row in self.store.search_fts(item.content, limit=6):
            if prefix_overlap(terms, row["content"]) >= min(2, len(terms)):
                found.setdefault(row["id"], row)
        names = [e.name for e in item.entities]
        if names:
            for row in self.store.active_memories_for_entity_names(names, limit=6):
                found.setdefault(row["id"], row)
        return list(found.values())[:6]

    def _exclusive_conflicts(self, item: ExtractedMemory) -> list[dict]:
        out: list[dict] = []
        for rel in item.relationships:
            if not _is_exclusive(rel.predicate):
                continue
            for cand in self.store.active_memories_for_relationship_signature(rel.subject, rel.predicate):
                if normalize_text(cand["relationship_object"]) != normalize_text(rel.object):
                    out.append(cand)
        return out

    def _heuristic_replacements(self, item: ExtractedMemory, semantic_match: dict | None) -> list[dict]:
        """No (working) judge: only replace on strong, structured evidence."""
        conflicts = self._exclusive_conflicts(item)
        if conflicts:
            return conflicts[:1]
        if (
            semantic_match
            and semantic_match["similarity"] >= self.heuristic_supersede_threshold
            and semantic_match.get("subtype") == item.subtype
            and item.subtype in _STATE_SUBTYPES
        ):
            shared = {normalize_text(n) for n in self.store.entity_names_for_memory(semantic_match["id"])} & {
                normalize_text(e.name) for e in item.entities
            }
            if shared:  # same subject, near-identical statement -> newer wins
                return [semantic_match]
        return []

    def _attach_structure(self, memory_id: str, item: ExtractedMemory) -> None:
        by_name: dict[str, str] = {}
        for ent in item.entities:
            entity_id = self.store.find_or_create_entity(ent.name, entity_type=ent.entity_type, aliases=ent.aliases)
            by_name[normalize_text(ent.name)] = entity_id
            self.store.link_memory_entity(memory_id, entity_id, role=ent.role)
        for rel in item.relationships:
            subject_id = by_name.get(normalize_text(rel.subject)) or self.store.find_or_create_entity(rel.subject)
            object_id = by_name.get(normalize_text(rel.object)) or self.store.find_or_create_entity(rel.object)
            rel_id = self.store.create_relationship(subject_id, rel.predicate, object_id, confidence=rel.confidence)
            self.store.link_memory_relationship(memory_id, rel_id)
