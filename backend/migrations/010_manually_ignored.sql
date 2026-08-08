-- Per-role dismiss for stuck incomplete profiles (idempotent).
ALTER TABLE role_candidates
  ADD COLUMN IF NOT EXISTS manually_ignored BOOLEAN NOT NULL DEFAULT false;
