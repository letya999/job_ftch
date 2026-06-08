CREATE TABLE IF NOT EXISTS jf_jobs (
    job_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_name TEXT NOT NULL,
    title TEXT,
    company TEXT,
    company_canonical TEXT,
    description TEXT NOT NULL,
    canonical_url TEXT,
    location TEXT,
    work_mode TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS jf_jobs_group_idx
ON jf_jobs (group_id);

CREATE INDEX IF NOT EXISTS jf_jobs_source_idx
ON jf_jobs (source_kind, source_name);

CREATE TABLE IF NOT EXISTS jf_job_groups (
    group_id TEXT PRIMARY KEY,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS jf_job_group_urls (
    canonical_url TEXT PRIMARY KEY,
    group_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jf_job_group_fingerprints (
    fingerprint TEXT PRIMARY KEY,
    group_id TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS jf_jobs_fts
USING fts5(
    job_id UNINDEXED,
    group_id UNINDEXED,
    title,
    company,
    company_canonical,
    description,
    tokenize = 'unicode61'
);
