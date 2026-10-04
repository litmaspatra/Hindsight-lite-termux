import sqlite3

import pytest

from hindsight_lite import MemoryStore
from hindsight_lite.schema import MIGRATIONS


def _age(store, memory_id, *, created="2020-01-01 00:00:00", updated=None):
    with store.transaction() as conn:
        conn.execute(
            "UPDATE memories SET created_at=?, updated_at=? WHERE id=?",
            (created, updated or created, memory_id),
        )


def test_upgrades_a_v3_database_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for version, script in MIGRATIONS[:3]:
        conn.executescript(script)
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,))
    conn.execute(
        "INSERT INTO memories(id, content, normalized_content, memory_type) VALUES ('m1','Peter likes jazz','peter likes jazz','world')"
    )
    conn.commit()
    conn.close()

    store = MemoryStore(path)
    assert store.stats()["schema_version"] == 4
    row = store.get_memory("m1")
    assert row["recall_count"] == 0 and row["last_recalled_at"] is None
    assert len(store.search_fts("jazz")) == 1          # FTS survived the trigger swap


def test_touch_recalled_counts_without_touching_updated_at_or_fts(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    mid = store.create_memory("Peter likes jazz", memory_type="world")
    _age(store, mid)
    store.touch_recalled([mid, mid, "nope"])
    row = store.get_memory(mid)
    assert row["recall_count"] == 1                    # duplicates in one call count once
    assert row["last_recalled_at"] is not None
    assert row["updated_at"] == "2020-01-01 00:00:00"  # recall is not an edit
    assert len(store.search_fts("jazz")) == 1


def test_touch_recalled_does_not_invalidate_the_vector_cache(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "m.db")
    mid = store.create_memory("north", memory_type="world")
    store.set_embedding(mid, [1.0, 0.0], provider="p", model="m")
    store.top_semantic([1.0, 0.0], provider="p", model="m")
    store.touch_recalled([mid])
    loads = []
    original = store._load_vectors
    monkeypatch.setattr(store, "_load_vectors", lambda *a, **k: (loads.append(1), original(*a, **k))[1])
    store.top_semantic([1.0, 0.0], provider="p", model="m")
    assert loads == []


def test_purge_memory_removes_row_fts_embedding_and_links(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    mid = store.create_memory("Peter likes jazz", memory_type="world")
    ent = store.find_or_create_entity("Peter")
    store.link_memory_entity(mid, ent)
    store.set_embedding(mid, [1.0, 0.0], provider="p", model="m")
    assert store.purge_memory(mid) is True
    assert store.get_memory(mid) is None
    assert store.search_fts("jazz") == []
    stats = store.stats()
    assert stats["embeddings"] == 0
    assert store.purge_memory(mid) is False


def test_stats_reports_inactive_counts_and_size(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    a = store.create_memory("a fact", memory_type="world")
    b = store.create_memory("b fact", memory_type="world")
    store.supersede_memory(a, b)
    c = store.create_memory("c fact", memory_type="world")
    store.soft_delete_memory(c)
    s = store.stats()
    assert (s["active_memories"], s["superseded_memories"], s["deleted_memories"]) == (1, 1, 1)
    assert s["db_bytes"] > 0
