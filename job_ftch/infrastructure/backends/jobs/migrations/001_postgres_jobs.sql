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
    raw_json JSONB NOT NULL,
    fts_vector TSVECTOR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS jf_jobs_group_idx
ON jf_jobs (group_id);

CREATE INDEX IF NOT EXISTS jf_jobs_source_idx
ON jf_jobs (source_kind, source_name);

CREATE INDEX IF NOT EXISTS jf_jobs_fts_idx
ON jf_jobs USING GIN (fts_vector);

CREATE TABLE IF NOT EXISTS jf_job_groups (
    group_id TEXT PRIMARY KEY,
    raw_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jf_job_group_urls (
    canonical_url TEXT PRIMARY KEY,
    group_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jf_job_group_fingerprints (
    fingerprint TEXT PRIMARY KEY,
    group_id TEXT NOT NULL
);
