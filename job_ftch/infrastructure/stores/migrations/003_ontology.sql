-- 003: Live ontology tables (per ADR-020)
-- Skills, roles, seniority, anti-patterns. Updated on-the-fly by LLM when shots are loaded.

CREATE TABLE IF NOT EXISTS jf_ontology_skill (
    canonical TEXT NOT NULL,
    alias     TEXT NOT NULL,
    lang      TEXT NOT NULL DEFAULT 'en',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (canonical, alias, lang)
);

CREATE INDEX IF NOT EXISTS jf_ontology_skill_alias_idx ON jf_ontology_skill (alias);
CREATE INDEX IF NOT EXISTS jf_ontology_skill_canonical_idx ON jf_ontology_skill (canonical);

CREATE TABLE IF NOT EXISTS jf_ontology_role (
    canonical TEXT NOT NULL,
    alias     TEXT NOT NULL,
    lang      TEXT NOT NULL DEFAULT 'en',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (canonical, alias, lang)
);

CREATE INDEX IF NOT EXISTS jf_ontology_role_alias_idx ON jf_ontology_role (alias);

CREATE TABLE IF NOT EXISTS jf_ontology_seniority (
    level TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS jf_ontology_anti (
    pattern TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS jf_ontology_positive_keyword (
    term TEXT PRIMARY KEY,
    weight INTEGER NOT NULL DEFAULT 1 CHECK(weight >= 1 AND weight <= 5)
);

CREATE TABLE IF NOT EXISTS jf_ontology_negative_keyword (
    term TEXT PRIMARY KEY,
    weight INTEGER NOT NULL DEFAULT 1 CHECK(weight >= 1 AND weight <= 5)
);
