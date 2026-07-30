-- 012: Corpus-level ontology term statistics.

CREATE TABLE IF NOT EXISTS jf_ontology_term_stat (
    entity_type TEXT NOT NULL,
    canonical TEXT NOT NULL,
    polarity TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    contextual_count INTEGER NOT NULL DEFAULT 0,
    positive_weight REAL NOT NULL DEFAULT 0.0,
    negative_weight REAL NOT NULL DEFAULT 0.0,
    contextual_weight REAL NOT NULL DEFAULT 0.0,
    recency_weight REAL NOT NULL DEFAULT 0.0,
    section_weight REAL NOT NULL DEFAULT 0.0,
    keyness REAL NOT NULL DEFAULT 0.0,
    score REAL NOT NULL DEFAULT 0.0,
    antonyms_json TEXT NOT NULL DEFAULT '[]',
    related_terms_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY(entity_type, canonical)
);

CREATE INDEX IF NOT EXISTS jf_ontology_term_stat_lookup_idx
    ON jf_ontology_term_stat(entity_type, polarity, score DESC);
