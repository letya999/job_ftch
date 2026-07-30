ALTER TABLE jf_outbox ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_jf_outbox_tenant_state ON jf_outbox(tenant_id, state);
