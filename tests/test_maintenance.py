from datetime import datetime, timezone

import pytest

from hindsight_lite import MemoryStore
from hindsight_lite.maintenance import MaintenancePolicy, maybe_run_maintenance, run_maintenance

OLD = "2020-01-01 00:00:00"


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "m.db")


def age(store, mid, *, created=OLD, updated=None):
    with store.transaction() as conn:
        conn.execute("UPDATE memories SET created_at=?, updated_at=? WHERE id=?", (created, updated or created, mid))


def mk(store, text, **kw):
    return store.create_memory(text, memory_type="world", **kw)


def exists(store, mid):
    return store.get_memory(mid) is not None


def test_superseded_memory_is_deleted_after_grace_period(store):
    old, new = mk(store, "likes jazz"), mk(store, "likes lo-fi")
    store.supersede_memory(old, new)
    age(store, old)                                     # superseded long ago
    report = run_maintenance(store, MaintenancePolicy(superseded_ttl_days=3))
    assert not exists(store, old) and exists(store, new)
    assert report["superseded_removed"] == 1


def test_recently_superseded_memory_survives_grace_period(store):
    old, new = mk(store, "likes jazz"), mk(store, "likes lo-fi")
    store.supersede_memory(old, new)
    run_maintenance(store, MaintenancePolicy(superseded_ttl_days=3))
    assert exists(store, old)


def test_ttl_zero_removes_superseded_and_deleted_immediately(store):
    old, new, gone = mk(store, "a"), mk(store, "b"), mk(store, "c")
    store.supersede_memory(old, new)
    store.soft_delete_memory(gone)
    report = run_maintenance(store, MaintenancePolicy(superseded_ttl_days=0))
    assert not exists(store, old) and not exists(store, gone) and exists(store, new)
    assert report["superseded_removed"] == 1 and report["deleted_removed"] == 1


def test_unused_low_importance_memory_expires(store):
    stale = mk(store, "mentioned once, never useful", importance=0.2)
    age(store, stale)
    report = run_maintenance(store, MaintenancePolicy(expire_unused_days=120, expire_max_importance=0.35))
    assert not exists(store, stale)
    assert report["expired_unused"] == 1


@pytest.mark.parametrize(
    "kwargs, recalled",
    [
        ({"importance": 0.8}, False),                    # important
        ({"importance": 0.2}, True),                     # was recalled at least once
        ({"importance": 0.2, "subtype": "directive"}, False),   # standing instruction
    ],
)
def test_valuable_memories_are_never_expired(store, kwargs, recalled):
    keep = mk(store, "keep me", **kwargs)
    age(store, keep)
    if recalled:
        store.touch_recalled([keep])
    run_maintenance(store, MaintenancePolicy())
    assert exists(store, keep)


def test_young_unused_memory_is_kept(store):
    young = mk(store, "new but unused", importance=0.1)
    run_maintenance(store, MaintenancePolicy())
    assert exists(store, young)


def test_orphan_entities_and_relationships_are_cleaned(store):
    dead, live = mk(store, "dead fact"), mk(store, "live fact")
    a, b, c = (store.find_or_create_entity(n) for n in ("Alpha", "Beta", "Gamma"))
    store.link_memory_entity(dead, a)
    store.link_memory_entity(live, c)
    rel = store.create_relationship(a, "knows", b)
    store.link_memory_relationship(dead, rel)
    store.soft_delete_memory(dead)
    run_maintenance(store, MaintenancePolicy(superseded_ttl_days=0))
    names = {e["canonical_name"] for e in store.list_entities()}
    assert names == {"Gamma"}
    assert store.stats()["relationships"] == 0


def test_old_retention_events_are_trimmed(store):
    mid = mk(store, "x")
    store.record_retention_event(action="created", memory_id=mid)
    with store.transaction() as conn:
        conn.execute("UPDATE retention_events SET created_at=?", (OLD,))
    store.record_retention_event(action="created", memory_id=mid)
    report = run_maintenance(store, MaintenancePolicy(event_log_days=90))
    assert report["events_removed"] == 1 and store.stats()["retention_events"] == 1


def test_dry_run_reports_but_deletes_nothing(store):
    old, new = mk(store, "a"), mk(store, "b")
    store.supersede_memory(old, new)
    report = run_maintenance(store, MaintenancePolicy(superseded_ttl_days=0), dry_run=True)
    assert report["superseded_removed"] == 1 and report["dry_run"] is True
    assert exists(store, old)


def test_maybe_run_is_throttled(store):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert maybe_run_maintenance(store, MaintenancePolicy(), min_interval_hours=6, now=now) is not None
    assert maybe_run_maintenance(store, MaintenancePolicy(), min_interval_hours=6, now=now.replace(hour=3)) is None
    assert maybe_run_maintenance(store, MaintenancePolicy(), min_interval_hours=6, now=now.replace(hour=7)) is not None
