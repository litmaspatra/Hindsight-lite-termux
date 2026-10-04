SCHEMA_VERSION = 4

MIGRATION_001 = r'''
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL CHECK(length(trim(content)) > 0),
    normalized_content TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK(memory_type IN ('world','experience','observation')),
    subtype TEXT,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    importance REAL NOT NULL DEFAULT 0.5 CHECK(importance >= 0.0 AND importance <= 1.0),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','deleted')),
    superseded_by TEXT REFERENCES memories(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_time TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type, subtype);
CREATE INDEX IF NOT EXISTS idx_memories_event_time ON memories(event_time);
CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    entity_type TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_name, entity_type)
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL CHECK(length(trim(predicate)) > 0),
    object_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    confidence REAL NOT NULL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_entity_id, predicate, object_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_subject ON relationships(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationship_object ON relationships(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationship_predicate ON relationships(predicate);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'mentioned',
    PRIMARY KEY(memory_id, entity_id, role)
);

CREATE TABLE IF NOT EXISTS memory_relationships (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    PRIMARY KEY(memory_id, relationship_id)
);

CREATE TABLE IF NOT EXISTS temporal_events (
    id TEXT PRIMARY KEY,
    memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
    event_time TEXT NOT NULL,
    end_time TEXT,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_temporal_events_time ON temporal_events(event_time);

CREATE TABLE IF NOT EXISTS directives (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL CHECK(length(trim(content)) > 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    normalized_content,
    subtype,
    source,
    content='memories',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memory_fts(rowid, content, normalized_content, subtype, source)
  VALUES (new.rowid, new.content, new.normalized_content, coalesce(new.subtype,''), coalesce(new.source,''));
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, content, normalized_content, subtype, source)
  VALUES ('delete', old.rowid, old.content, old.normalized_content, coalesce(old.subtype,''), coalesce(old.source,''));
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, content, normalized_content, subtype, source)
  VALUES ('delete', old.rowid, old.content, old.normalized_content, coalesce(old.subtype,''), coalesce(old.source,''));
  INSERT INTO memory_fts(rowid, content, normalized_content, subtype, source)
  VALUES (new.rowid, new.content, new.normalized_content, coalesce(new.subtype,''), coalesce(new.source,''));
END;
'''

MIGRATION_002 = r'''
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK(length(trim(provider)) > 0),
    model TEXT NOT NULL CHECK(length(trim(model)) > 0),
    dimension INTEGER NOT NULL CHECK(dimension > 0 AND dimension <= 16384),
    vector BLOB NOT NULL,
    norm REAL NOT NULL CHECK(norm > 0.0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_backend
ON memory_embeddings(provider, model, dimension);

CREATE TRIGGER IF NOT EXISTS memories_embedding_invalidate
AFTER UPDATE OF content ON memories
WHEN old.content <> new.content
BEGIN
    DELETE FROM memory_embeddings WHERE memory_id = new.id;
END;
'''

MIGRATION_003 = r'''
CREATE TABLE IF NOT EXISTS retention_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL CHECK(action IN ('created','updated','superseded','ignored')),
    memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
    superseded_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
    reason TEXT NOT NULL DEFAULT '',
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_retention_events_created_at
ON retention_events(created_at);
'''

MIGRATION_004 = r'''
ALTER TABLE memories ADD COLUMN last_recalled_at TEXT;
ALTER TABLE memories ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_memories_status_updated ON memories(status, updated_at);

-- Bookkeeping updates (recall_count, status, superseded_by...) must not rewrite the FTS row.
DROP TRIGGER IF EXISTS memories_au;
CREATE TRIGGER memories_au AFTER UPDATE OF content, normalized_content, subtype, source ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, content, normalized_content, subtype, source)
  VALUES ('delete', old.rowid, old.content, old.normalized_content, coalesce(old.subtype,''), coalesce(old.source,''));
  INSERT INTO memory_fts(rowid, content, normalized_content, subtype, source)
  VALUES (new.rowid, new.content, new.normalized_content, coalesce(new.subtype,''), coalesce(new.source,''));
END;
'''

MIGRATIONS = (
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
)
