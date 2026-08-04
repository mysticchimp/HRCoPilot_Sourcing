-- Track whether raw_profile is a Full Apify profile vs a thin Short stub.
ALTER TABLE candidates
  ADD COLUMN IF NOT EXISTS is_complete_profile BOOLEAN NOT NULL DEFAULT true;

-- Backfill: Short stubs lack experience[] (and usually skills/about). Mark incomplete.
UPDATE candidates
SET is_complete_profile = false
WHERE raw_profile IS NULL
   OR jsonb_typeof(raw_profile->'experience') IS DISTINCT FROM 'array'
   OR jsonb_array_length(COALESCE(raw_profile->'experience', '[]'::jsonb)) = 0;
