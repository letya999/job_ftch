CREATE TABLE IF NOT EXISTS jf_source_operator_flags (
    tenant_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    important INTEGER NOT NULL DEFAULT 0,
    set_by TEXT NOT NULL DEFAULT 'operator',
    set_at TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (tenant_id, source_key)
);

CREATE TABLE IF NOT EXISTS jf_pipeline_run_stats (
    tenant_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    ok_sources INTEGER NOT NULL DEFAULT 0,
    fail_sources INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    extracted INTEGER NOT NULL DEFAULT 0,
    emitted INTEGER NOT NULL DEFAULT 0,
    review INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    llm_tokens_in INTEGER NOT NULL DEFAULT 0,
    llm_tokens_out INTEGER NOT NULL DEFAULT 0,
    llm_latency_ms INTEGER NOT NULL DEFAULT 0,
    llm_cost_usd REAL NOT NULL DEFAULT 0,
    conversion_extract REAL NOT NULL DEFAULT 0,
    conversion_accept REAL NOT NULL DEFAULT 0,
    extra_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, source_run_id)
);

CREATE INDEX IF NOT EXISTS jf_pipeline_run_stats_finished
    ON jf_pipeline_run_stats (tenant_id, finished_at);

CREATE TABLE IF NOT EXISTS jf_source_run_stats (
    tenant_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    yielded INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    extracted INTEGER NOT NULL DEFAULT 0,
    emitted INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    llm_latency_ms INTEGER NOT NULL DEFAULT 0,
    llm_cost_usd REAL NOT NULL DEFAULT 0,
    conversion_accept REAL NOT NULL DEFAULT 0,
    quality_reliable INTEGER NOT NULL DEFAULT 0,
    quality_rich INTEGER NOT NULL DEFAULT 0,
    quality_high_relevance INTEGER NOT NULL DEFAULT 0,
    quality_important INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (tenant_id, source_run_id, source_id)
);

CREATE INDEX IF NOT EXISTS jf_source_run_stats_key
    ON jf_source_run_stats (tenant_id, source_key, started_at);
