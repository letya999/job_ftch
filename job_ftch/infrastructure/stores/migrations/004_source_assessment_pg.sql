CREATE TABLE IF NOT EXISTS jf_source_assessments (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    assessed_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, source_id)
);

CREATE TABLE IF NOT EXISTS jf_source_ingest_state (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    bootstrap_completed_at TIMESTAMPTZ NULL,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, source_id)
);
