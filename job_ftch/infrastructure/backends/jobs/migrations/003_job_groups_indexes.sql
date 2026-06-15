-- Migration: Performance indexes for jf_job_groups

-- Temporal sort: list_groups ORDER BY updated_at DESC
CREATE INDEX IF NOT EXISTS jf_job_groups_updated_idx
    ON public.jf_job_groups USING btree (updated_at DESC);

-- post_type filter: WHERE raw_json->'canonical_job'->>'post_type' = 'job_posting'
CREATE INDEX IF NOT EXISTS jf_job_groups_post_type_idx
    ON public.jf_job_groups
    USING btree (((raw_json -> 'canonical_job' ->> 'post_type')));

-- source_kind filter: WHERE raw_json->'canonical_job'->>'source_kind' = 'career_site'
CREATE INDEX IF NOT EXISTS jf_job_groups_source_kind_idx
    ON public.jf_job_groups
    USING btree (((raw_json -> 'canonical_job' ->> 'source_kind')));
