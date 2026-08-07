-- LLM narrative cache on scored role_candidates (idempotent).
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS summary_text TEXT NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS assessment_text TEXT NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS narrative_generated_at TIMESTAMPTZ NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS narrative_jd_hash TEXT NULL;
