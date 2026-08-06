-- Parsed scoring brief (JobRoleSchema JSON) alongside free-text JD.
ALTER TABLE roles ADD COLUMN IF NOT EXISTS jd_parsed JSONB NULL;

-- Per-candidate audit: which /score mode produced this score ("parsed" | "llm").
ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS scoring_mode TEXT NULL;
