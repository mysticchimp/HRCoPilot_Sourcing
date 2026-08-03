-- JD text on roles + score fields on role_candidates (idempotent).
ALTER TABLE roles ADD COLUMN IF NOT EXISTS jd_text TEXT NULL;

ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS total_score NUMERIC NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS component_breakdown JSONB NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS matched_signals TEXT[] NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS reasoning TEXT NULL;
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ NULL;
