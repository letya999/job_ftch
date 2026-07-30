-- Remove job groups with no canonical_job post_type (pre-LLM-era records).
DELETE FROM jf_job_groups
WHERE json_extract(raw_json, '$.canonical_job.post_type') IS NULL
  AND updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 day');
