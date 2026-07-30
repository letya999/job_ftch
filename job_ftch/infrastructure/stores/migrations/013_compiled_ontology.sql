-- 013: Profile-level compiled ontology source of truth.

CREATE TABLE IF NOT EXISTS jf_ontology_compiled_term (
    canonical TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    semantic_role TEXT NOT NULL,
    polarity TEXT NOT NULL,
    scope TEXT NOT NULL,
    source_section TEXT NOT NULL DEFAULT 'unknown',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    evidence_shot_ids_json TEXT NOT NULL DEFAULT '[]',
    support_count INTEGER NOT NULL DEFAULT 0,
    contrast_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    weight REAL NOT NULL DEFAULT 0.0,
    accepted INTEGER NOT NULL DEFAULT 0,
    reject_reason TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY(entity_type, canonical)
);

CREATE TABLE IF NOT EXISTS jf_ontology_compiled_relation (
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'contextual',
    evidence_shot_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    weight REAL NOT NULL DEFAULT 0.0,
    rationale TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY(subject, predicate, object)
);

CREATE INDEX IF NOT EXISTS jf_ontology_compiled_term_lookup_idx
    ON jf_ontology_compiled_term(entity_type, semantic_role, accepted, weight DESC);

CREATE INDEX IF NOT EXISTS jf_ontology_compiled_relation_lookup_idx
    ON jf_ontology_compiled_relation(subject, predicate, object);
