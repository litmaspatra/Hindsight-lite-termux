import time

import pytest

from hindsight_lite import MemoryStore

KW = dict(provider="p", model="m")


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    s.ids = []
    for text, vec in (("north", [1.0, 0.0, 0.0]), ("east", [0.0, 1.0, 0.0]), ("northeast", [1.0, 1.0, 0.0])):
        mid = s.create_memory(text, memory_type="world")
        s.set_embedding(mid, vec, **KW)
        s.ids.append(mid)
    return s


def test_top_semantic_ranks_by_cosine(store):
    rows = store.top_semantic([1.0, 0.1, 0.0], limit=3, **KW)
    assert [r["content"] for r in rows] == ["north", "northeast", "east"]
    assert rows[0]["semantic_score"] > rows[1]["semantic_score"] > rows[2]["semantic_score"]


def test_top_semantic_min_score_filters(store):
    rows = store.top_semantic([1.0, 0.0, 0.0], limit=3, min_score=0.9, **KW)
    assert [r["content"] for r in rows] == ["north"]


def test_cache_sees_new_embedding_immediately(store):
    store.top_semantic([1.0, 0.0, 0.0], limit=1, **KW)           # warm the cache
    mid = store.create_memory("up", memory_type="world")
    store.set_embedding(mid, [0.0, 0.0, 1.0], **KW)
    assert store.top_semantic([0.0, 0.0, 1.0], limit=1, **KW)[0]["content"] == "up"


def test_cache_drops_superseded_and_deleted_memories(store):
    north, east, _ = store.ids
    store.top_semantic([1.0, 0.0, 0.0], limit=3, **KW)
    store.soft_delete_memory(north)
    contents = [r["content"] for r in store.top_semantic([1.0, 0.0, 0.0], limit=3, **KW)]
    assert "north" not in contents
    store.supersede_memory(east, store.ids[2])
    contents = [r["content"] for r in store.top_semantic([0.0, 1.0, 0.0], limit=3, **KW)]
    assert contents == ["northeast"]


def test_cache_is_actually_used(store, monkeypatch):
    store.top_semantic([1.0, 0.0, 0.0], limit=1, **KW)
    calls = []
    original = store._load_vectors
    monkeypatch.setattr(store, "_load_vectors", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    store.top_semantic([1.0, 0.0, 0.0], limit=1, **KW)
    assert calls == []
