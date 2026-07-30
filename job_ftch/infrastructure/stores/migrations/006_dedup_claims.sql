CREATE TABLE IF NOT EXISTS jf_dedup_claims (
    claim_key TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jf_dedup_claims_expires_at ON jf_dedup_claims(expires_at);
