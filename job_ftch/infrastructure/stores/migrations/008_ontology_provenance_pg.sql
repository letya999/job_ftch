-- 008: Ontology Provenance (Postgres) - idempotent
ALTER TABLE jf_ontology_skill ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'positive';
ALTER TABLE jf_ontology_skill ADD COLUMN IF NOT EXISTS source_shot_id TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN IF NOT EXISTS prompt_hash TEXT;

ALTER TABLE jf_ontology_role ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'positive';
ALTER TABLE jf_ontology_role ADD COLUMN IF NOT EXISTS source_shot_id TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN IF NOT EXISTS prompt_hash TEXT;
