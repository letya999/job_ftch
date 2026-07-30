-- 009: append-only ontology evidence for SQLite.
-- Compatibility tables retain aliases; policy views read this occurrence ledger.
CREATE TABLE IF NOT EXISTS jf_ontology_occurrence (
    entity_type TEXT NOT NULL CHECK(entity_type IN ('skill', 'role')),
    canonical TEXT NOT NULL,
    alias TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'en',
    polarity TEXT NOT NULL CHECK(polarity IN ('positive', 'negative')),
    source_shot_id TEXT NOT NULL,
    source_type TEXT,
    model TEXT,
    prompt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY(entity_type, canonical, alias, lang, polarity, source_shot_id, prompt_hash)
);

CREATE INDEX IF NOT EXISTS jf_ontology_occurrence_lookup_idx
    ON jf_ontology_occurrence(entity_type, polarity, lang, canonical);
