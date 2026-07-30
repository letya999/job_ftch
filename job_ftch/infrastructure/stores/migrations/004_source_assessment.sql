CREATE TABLE IF NOT EXISTS jf_source_assessments (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, source_id)
);

CREATE TABLE IF NOT EXISTS jf_source_ingest_state (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    bootstrap_completed_at TEXT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, source_id)
);
