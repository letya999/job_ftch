-- 012: Corpus-level ontology term statistics (PostgreSQL flavor).

CREATE TABLE IF NOT EXISTS jf_ontology_term_stat (
    entity_type TEXT NOT NULL,
    canonical TEXT NOT NULL,
    polarity TEXT NOT NULL,
    aliases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    contextual_count INTEGER NOT NULL DEFAULT 0,
    positive_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    negative_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    contextual_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    recency_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    section_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    keyness DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    antonyms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_terms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(entity_type, canonical)
);

CREATE INDEX IF NOT EXISTS jf_ontology_term_stat_lookup_idx
    ON jf_ontology_term_stat(entity_type, polarity, score DESC);
