CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uri TEXT DEFAULT '',
    tags JSONB DEFAULT '[]',
    state TEXT DEFAULT 'ingesting',
    chunk_count INT DEFAULT 0,
    error TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    seq INT NOT NULL,
    text TEXT NOT NULL,
    token_count INT DEFAULT 0,
    embedding_model TEXT NOT NULL,
    char_start INT DEFAULT 0,
    char_end INT DEFAULT 0,
    heading_path JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS kb_chunks_doc ON kb_chunks(doc_id, seq);
CREATE INDEX IF NOT EXISTS kb_chunks_user ON kb_chunks(user_id);

INSERT INTO _schema_meta(version, description)
VALUES (12, 'kb') ON CONFLICT DO NOTHING;
