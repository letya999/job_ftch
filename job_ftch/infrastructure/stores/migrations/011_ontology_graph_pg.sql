-- 011: Shot-derived ontology graph (PostgreSQL flavor).

CREATE TABLE IF NOT EXISTS jf_ontology_graph_version (
    graph_id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jf_ontology_node (
    node_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical TEXT NOT NULL,
    display TEXT,
    lang TEXT NOT NULL DEFAULT 'en',
    attrs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jf_ontology_edge (
    edge_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    subject_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_node_id TEXT NOT NULL,
    polarity TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL CHECK(weight >= 0.0 AND weight <= 1.0),
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    arity_group_id TEXT,
    attrs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jf_ontology_evidence (
    evidence_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    edge_id TEXT,
    source_shot_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_section TEXT NOT NULL,
    text_span TEXT NOT NULL DEFAULT '',
    extraction_confidence DOUBLE PRECISION NOT NULL CHECK(extraction_confidence >= 0.0 AND extraction_confidence <= 1.0),
    model TEXT,
    prompt_hash TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jf_ontology_node_kind_canonical_idx
    ON jf_ontology_node(kind, canonical);
CREATE INDEX IF NOT EXISTS jf_ontology_edge_lookup_idx
    ON jf_ontology_edge(graph_id, predicate, polarity);
CREATE INDEX IF NOT EXISTS jf_ontology_evidence_shot_idx
    ON jf_ontology_evidence(source_shot_id, source_type);
