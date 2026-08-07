-- Cap automatic Full-enrich retries for incomplete (Short stub) profiles.
-- After MAX attempts (app-enforced), UI shows "failed after N attempts" and
-- retry-incomplete skips them so Apify spend does not loop forever.
ALTER TABLE candidates
  ADD COLUMN IF NOT EXISTS enrich_retry_count INTEGER NOT NULL DEFAULT 0;
