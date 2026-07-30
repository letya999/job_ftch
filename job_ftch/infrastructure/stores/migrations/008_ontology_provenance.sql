-- 008: Ontology Provenance
ALTER TABLE jf_ontology_skill ADD COLUMN polarity TEXT NOT NULL DEFAULT 'positive';
ALTER TABLE jf_ontology_skill ADD COLUMN source_shot_id TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN source_type TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN model TEXT;
ALTER TABLE jf_ontology_skill ADD COLUMN prompt_hash TEXT;

ALTER TABLE jf_ontology_role ADD COLUMN polarity TEXT NOT NULL DEFAULT 'positive';
ALTER TABLE jf_ontology_role ADD COLUMN source_shot_id TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN source_type TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN model TEXT;
ALTER TABLE jf_ontology_role ADD COLUMN prompt_hash TEXT;
