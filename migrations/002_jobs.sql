CREATE TABLE IF NOT EXISTS async_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    priority INT DEFAULT 0,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    locked_by TEXT,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS jobs_pending ON async_jobs(state, priority DESC, scheduled_at)
    WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS jobs_type ON async_jobs(job_type, state);

INSERT INTO _schema_meta(version, description)
VALUES (2, 'async_jobs') ON CONFLICT DO NOTHING;
