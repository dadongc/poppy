CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    mime_type TEXT NOT NULL,
    encoding TEXT DEFAULT 'utf-8',
    summary TEXT DEFAULT '',
    preview TEXT,
    source_type TEXT NOT NULL,
    source_run_id TEXT,
    source_session_id TEXT,
    source_tool_name TEXT,
    source_call_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0,
    state TEXT DEFAULT 'active',
    expires_at TIMESTAMPTZ,
    pinned BOOLEAN DEFAULT FALSE,
    title TEXT DEFAULT '',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS artifacts_user ON artifacts(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS artifacts_hash ON artifacts(user_id, content_hash);
CREATE INDEX IF NOT EXISTS artifacts_expires ON artifacts(expires_at)
    WHERE state = 'active' AND expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS artifact_blob_refs (
    user_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    refcount INT NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, content_hash)
);

INSERT INTO _schema_meta(version, description)
VALUES (11, 'artifacts') ON CONFLICT DO NOTHING;
