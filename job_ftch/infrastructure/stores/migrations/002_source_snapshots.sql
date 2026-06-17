CREATE TABLE IF NOT EXISTS jf_source_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    stable_id TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    item_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jf_source_snapshots_lookup
    ON jf_source_snapshots (tenant_id, source_id, stable_id, run_at DESC);
