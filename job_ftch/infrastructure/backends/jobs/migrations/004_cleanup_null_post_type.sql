-- Remove job groups with no canonical_job post_type (pre-LLM-era records).
-- These are old records from pipeline runs before post_type extraction was added.
-- Safe to delete: they have no usable post_type signal and clutter reranking.
DELETE FROM jf_job_groups
WHERE (raw_json -> 'canonical_job' ->> 'post_type') IS NULL
  AND created_at < NOW() - INTERVAL '1 day';
