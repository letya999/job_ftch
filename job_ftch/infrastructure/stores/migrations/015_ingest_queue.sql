CREATE TABLE IF NOT EXISTS jf_ingest_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    rate_scope TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_items INTEGER,
    user_id TEXT,
    bypass_override TEXT,
    parser_override TEXT,
    personal_mode INTEGER NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL,
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_until TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS jf_ingest_tasks_due
    ON jf_ingest_tasks (tenant_id, state, available_at);
CREATE INDEX IF NOT EXISTS jf_ingest_tasks_run
    ON jf_ingest_tasks (tenant_id, run_id, state);

CREATE TABLE IF NOT EXISTS jf_ingest_rate_limits (
    scope_id TEXT PRIMARY KEY,
    cooldown_until TEXT NOT NULL,
    retry_after_seconds REAL,
    status_code INTEGER,
    updated_at TEXT NOT NULL
);
