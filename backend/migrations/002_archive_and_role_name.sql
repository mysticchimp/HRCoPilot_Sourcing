-- Soft-archive roles + denormalized role_name snapshot on role_candidates.

ALTER TABLE roles ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;

ALTER TABLE role_candidates ADD COLUMN IF NOT EXISTS role_name TEXT NOT NULL DEFAULT '';

-- Backfill existing rows from the parent role name at migration time.
UPDATE role_candidates rc
SET role_name = r.role_name
FROM roles r
WHERE r.id = rc.role_id
  AND rc.role_name = '';
