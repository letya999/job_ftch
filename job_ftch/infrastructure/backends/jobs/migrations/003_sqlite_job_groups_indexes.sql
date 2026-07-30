-- Migration: Performance indexes for jf_job_groups

-- Temporal sort: list_groups ORDER BY updated_at DESC
CREATE INDEX IF NOT EXISTS jf_job_groups_updated_idx
    ON jf_job_groups (updated_at DESC);

-- post_type filter
CREATE INDEX IF NOT EXISTS jf_job_groups_post_type_idx
    ON jf_job_groups (json_extract(raw_json, '$.canonical_job.post_type'));

-- source_kind filter
CREATE INDEX IF NOT EXISTS jf_job_groups_source_kind_idx
    ON jf_job_groups (json_extract(raw_json, '$.canonical_job.source_kind'));
