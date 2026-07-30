-- Migration: Add blocking_key to jf_job_groups
ALTER TABLE jf_job_groups ADD COLUMN IF NOT EXISTS blocking_key TEXT;
CREATE INDEX IF NOT EXISTS jf_job_groups_blocking_key_idx ON jf_job_groups (blocking_key);
