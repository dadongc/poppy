CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    message_count INT DEFAULT 0,
    summary TEXT DEFAULT '',
    summary_covers_until_seq INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS sessions_user_active ON sessions(user_id, last_active_at DESC);

CREATE TABLE IF NOT EXISTS session_messages (
    msg_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    seq INT NOT NULL,
    run_id TEXT DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB DEFAULT '[]',
    tool_call_id TEXT DEFAULT '',
    name TEXT DEFAULT '',
    artifact_refs JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS session_msgs_seq ON session_messages(session_id, seq);

INSERT INTO _schema_meta(version, description)
VALUES (10, 'sessions') ON CONFLICT DO NOTHING;
