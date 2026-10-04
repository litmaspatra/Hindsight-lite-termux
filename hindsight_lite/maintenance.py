"""Automatic clean-up so the database never fills with dead facts.

Removes, in order:
  1. superseded memories after a short grace period (the replacement already
     carries any "previously ..." history, the grace period is an undo window),
  2. soft-deleted memories after the same period,
  3. low-importance memories that were never recalled and are old ("no use"),
  4. relationships/entities left with no memory pointing at them,
  5. old retention-event log rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import MemoryStore

_FMT = "%Y-%m-%d %H:%M:%S"
_VACUUM_THRESHOLD = 25  # rewrite the file only when a run frees a meaningful amount


@dataclass(frozen=True)
class MaintenancePolicy:
    superseded_ttl_days: float = 3.0      # 0 = delete immediately
    expire_unused_days: float = 120.0     # 0 = never expire unused memories
    expire_max_importance: float = 0.35   # only memories below this are expirable
    event_log_days: float = 90.0


def _cutoff(now: datetime, days: float) -> str:
    return (now - timedelta(days=float(days))).astimezone(timezone.utc).strftime(_FMT)


def _ids(conn, sql: str, params: tuple) -> list[str]:
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def _delete_ids(conn, ids: list[str]) -> None:
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        conn.execute(f"DELETE FROM memories WHERE id IN ({','.join('?' for _ in chunk)})", chunk)


def run_maintenance(
    store: MemoryStore,
    policy: MaintenancePolicy | None = None,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    policy = policy or MaintenancePolicy()
    now = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "dry_run": dry_run, "superseded_removed": 0, "deleted_removed": 0, "expired_unused": 0,
        "orphan_relationships": 0, "orphan_entities": 0, "events_removed": 0,
    }
    ttl = _cutoff(now, policy.superseded_ttl_days)
    with store.transaction() as conn:
        superseded = _ids(conn, "SELECT id FROM memories WHERE status='superseded' AND updated_at <= ?", (ttl,))
        deleted = _ids(conn, "SELECT id FROM memories WHERE status='deleted' AND updated_at <= ?", (ttl,))
        expired: list[str] = []
        if policy.expire_unused_days > 0:
            expired = _ids(
                conn,
                """SELECT id FROM memories
                   WHERE status='active' AND recall_count = 0 AND importance < ?
                     AND COALESCE(subtype,'') != 'directive' AND created_at <= ?""",
                (policy.expire_max_importance, _cutoff(now, policy.expire_unused_days)),
            )
        report.update(superseded_removed=len(superseded), deleted_removed=len(deleted), expired_unused=len(expired))
        events_cutoff = _cutoff(now, policy.event_log_days)
        report["events_removed"] = conn.execute(
            "SELECT COUNT(*) FROM retention_events WHERE created_at <= ?", (events_cutoff,)
        ).fetchone()[0]
        if dry_run:
            conn.rollback()
            return report
        _delete_ids(conn, superseded + deleted + expired)
        conn.execute("DELETE FROM retention_events WHERE created_at <= ?", (events_cutoff,))
        report["orphan_relationships"] = conn.execute(
            "DELETE FROM relationships WHERE id NOT IN (SELECT relationship_id FROM memory_relationships)"
        ).rowcount
        report["orphan_entities"] = conn.execute(
            """DELETE FROM entities
               WHERE id NOT IN (SELECT entity_id FROM memory_entities)
                 AND id NOT IN (SELECT subject_entity_id FROM relationships)
                 AND id NOT IN (SELECT object_entity_id FROM relationships)"""
        ).rowcount
    removed = len(superseded) + len(deleted) + len(expired)
    if removed >= _VACUUM_THRESHOLD:
        with store._connect() as conn:
            conn.isolation_level = None
            conn.execute("VACUUM")
    return report


def maybe_run_maintenance(
    store: MemoryStore,
    policy: MaintenancePolicy | None = None,
    *,
    min_interval_hours: float = 6.0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Run at most once per interval. Returns the report, or None if skipped."""
    now = now or datetime.now(timezone.utc)
    with store._connect() as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='last_maintenance_at'").fetchone()
    if row:
        try:
            last = datetime.fromisoformat(row["value"])
            if now - last < timedelta(hours=min_interval_hours):
                return None
        except ValueError:
            pass
    report = run_maintenance(store, policy, now=now)
    with store.transaction(bump=False) as conn:
        conn.execute(
            """INSERT INTO metadata(key, value) VALUES ('last_maintenance_at', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (now.isoformat(),),
        )
    return report
