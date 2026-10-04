import os

import pytest

from hindsight_lite import MemoryStore, ObsidianMirror


@pytest.fixture
def setup(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    mirror = ObsidianMirror(store, tmp_path / "vault")
    a = store.create_memory("Peter likes jazz", memory_type="world")
    store.link_memory_entity(a, store.find_or_create_entity("Peter"))
    return store, mirror, a, tmp_path / "vault"


def test_second_sync_with_no_changes_writes_nothing(setup):
    store, mirror, *_ = setup
    first = mirror.sync()
    assert first.written > 0 and first.unchanged == 0
    stamp = {p: p.stat().st_mtime_ns for p in mirror.vault.rglob("*.md")}
    second = mirror.sync()
    assert second.written == 0 and second.unchanged == first.written
    assert {p: p.stat().st_mtime_ns for p in mirror.vault.rglob("*.md")} == stamp


def test_only_changed_notes_are_rewritten(setup):
    store, mirror, a, _ = setup
    mirror.sync()
    store.update_memory(a, content="Peter likes lo-fi")
    report = mirror.sync()
    assert 0 < report.written < report.written + report.unchanged


def test_superseded_and_deleted_memories_stay_out_of_the_vault(setup):
    store, mirror, a, vault = setup
    b = store.create_memory("Peter likes lo-fi", memory_type="world")
    mirror.sync()
    assert (vault / "Memories" / f"{a}.md").exists()
    store.supersede_memory(a, b)
    mirror.sync()
    assert not (vault / "Memories" / f"{a}.md").exists()
    assert (vault / "Memories" / f"{b}.md").exists()


def test_never_overwrites_a_users_own_note(setup):
    store, mirror, a, vault = setup
    mirror.sync()
    note = vault / "Memories" / f"{a}.md"
    note.write_text("my own note")
    with pytest.raises(FileExistsError):
        mirror.sync()
