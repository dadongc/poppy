CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error TEXT DEFAULT '',
    used_tokens INT NOT NULL DEFAULT 0,
    used_steps INT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_user_started ON runs(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS run_closure (
    ancestor TEXT NOT NULL,
    descendant TEXT NOT NULL,
    depth INT NOT NULL DEFAULT 0,
    PRIMARY KEY (ancestor, descendant)
);
CREATE INDEX IF NOT EXISTS idx_run_closure_anc ON run_closure(ancestor);
CREATE INDEX IF NOT EXISTS idx_run_closure_desc ON run_closure(descendant);

INSERT INTO _schema_meta(version, description)
VALUES (20, 'runs + run_closure') ON CONFLICT DO NOTHING;
