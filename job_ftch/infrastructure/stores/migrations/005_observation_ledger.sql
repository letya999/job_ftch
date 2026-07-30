CREATE TABLE IF NOT EXISTS jf_observations (
    tenant_id TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, stable_id, content_hash),
    UNIQUE (tenant_id, stable_id, content_version)
);
