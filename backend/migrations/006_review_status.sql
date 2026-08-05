-- Per-role review queue status on scored candidates (idempotent).
ALTER TABLE role_candidates
  ADD COLUMN IF NOT EXISTS review_status text NOT NULL DEFAULT 'reviewing';

ALTER TABLE role_candidates DROP CONSTRAINT IF EXISTS role_candidates_review_status_check;
ALTER TABLE role_candidates
  ADD CONSTRAINT role_candidates_review_status_check
  CHECK (review_status IN ('reviewing', 'shortlisted', 'benched'));
