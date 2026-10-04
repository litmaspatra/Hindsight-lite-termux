"""Bug 2: prefetch must not inject irrelevant memories. Bug 3: it must not block on a dead endpoint."""
import pytest

from hindsight_lite import MemoryStore, RetrievalEngine
from hindsight_lite.embeddings import CircuitBreaker

from .helpers import FailingEmbedder, HashEmbedder


@pytest.fixture
def engine(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    emb = HashEmbedder()
    eng = RetrievalEngine(store, embedding_backend=emb)
    a = store.create_memory("Peter prefers dark mode in Obsidian", memory_type="world", subtype="preference")
    b = store.create_memory("Termux sync runs nightly over wifi", memory_type="world", subtype="procedure")
    ent_a = store.find_or_create_entity("Obsidian")
    ent_b = store.find_or_create_entity("Termux")
    store.link_memory_entity(a, ent_a)
    store.link_memory_entity(b, ent_b)
    store.create_relationship(ent_a, "synced_with", ent_b)
    eng.index_memories([a, b])
    eng.ids = (a, b)
    return eng


def test_irrelevant_query_injects_nothing(engine):
    result = engine.recall_for_context("quantum chromodynamics lattice simulation", min_semantic=0.35)
    assert result.hits == []


def test_relevant_query_returns_the_memory(engine):
    result = engine.recall_for_context("which theme does Peter use in Obsidian", min_semantic=0.35)
    assert [h.memory_id for h in result.hits] == [engine.ids[0]]
    assert "lexical" in result.hits[0].sources


def test_graph_only_neighbours_are_not_injected_but_still_recallable(engine):
    # Query names only "Obsidian"; the Termux memory is reachable solely via the graph arm.
    ctx = engine.recall_for_context("Obsidian", min_semantic=0.99)
    assert engine.ids[1] not in [h.memory_id for h in ctx.hits]
    full = engine.recall("Obsidian", limit=10)
    assert engine.ids[1] in [h.memory_id for h in full]


def test_weak_semantic_match_is_rejected_strong_one_accepted(engine):
    weak = engine.recall_for_context("zzzz yyyy Peter xxxx wwww vvvv uuuu", min_semantic=0.5, use_semantic=True)
    assert weak.hits == []
    strong = engine.recall_for_context("Peter prefers dark mode in Obsidian", min_semantic=0.5)
    assert strong.hits and "semantic" in strong.hits[0].sources


def test_use_semantic_false_never_calls_the_embedder(engine):
    before = engine.embedding_backend.calls
    engine.recall_for_context("Peter prefers dark mode", use_semantic=False)
    assert engine.embedding_backend.calls == before


def test_embedder_failure_degrades_to_local_arms_and_reports_error(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.create_memory("Peter prefers dark mode in Obsidian", memory_type="world")
    eng = RetrievalEngine(store, embedding_backend=FailingEmbedder())
    result = eng.recall_for_context("dark mode obsidian")
    assert len(result.hits) == 1
    assert result.semantic_error == "EmbeddingError"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_circuit_breaker_opens_after_failures_and_recovers_after_cooldown():
    clock = _Clock()
    br = CircuitBreaker(failure_threshold=2, cooldown_seconds=60, clock=clock)
    assert br.allow()
    br.record(False)
    assert br.allow()
    br.record(False)
    assert not br.allow()          # open: skip the network entirely
    clock.t = 59
    assert not br.allow()
    clock.t = 61
    assert br.allow()              # half-open: one probe
    br.record(True)
    assert br.allow()


def test_circuit_breaker_success_resets_failure_count():
    br = CircuitBreaker(failure_threshold=2, cooldown_seconds=60, clock=_Clock())
    br.record(False)
    br.record(True)
    br.record(False)
    assert br.allow()
