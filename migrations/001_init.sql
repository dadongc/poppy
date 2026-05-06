CREATE TABLE IF NOT EXISTS _schema_meta (
    version INT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    parent_run_id TEXT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    trace_id TEXT,
    ts TIMESTAMPTZ NOT NULL,
    seq INT NOT NULL,
    payload JSONB DEFAULT '{}',
    level TEXT DEFAULT 'info',
    scope TEXT DEFAULT 'public'
);
CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS events_session_ts ON events(session_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_user_ts ON events(user_id, ts DESC);

INSERT INTO _schema_meta(version, description)
VALUES (1, 'init') ON CONFLICT DO NOTHING;
