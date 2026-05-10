CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_run_id TEXT,
    source_session_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_recalled_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ,
    confidence DOUBLE PRECISION DEFAULT 1.0,
    importance DOUBLE PRECISION DEFAULT 0.5,
    recall_count INT DEFAULT 0,
    state TEXT DEFAULT 'active',
    related_memory_ids JSONB DEFAULT '[]',
    artifact_refs JSONB DEFAULT '[]',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS mem_user_kind ON memory_records(user_id, kind, state);
CREATE INDEX IF NOT EXISTS mem_user_active_recall ON memory_records(
    user_id, state, last_recalled_at DESC NULLS LAST
) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS mem_tags ON memory_records USING GIN(tags);

INSERT INTO _schema_meta(version, description)
VALUES (13, 'memory') ON CONFLICT DO NOTHING;
