-- Persist the actor input that actually returned hits after probe_with_relax
-- (e.g. searchQuery dropped). Retries/re-pulls can start from this instead of
-- re-discovering via trial and error.
ALTER TABLE roles
  ADD COLUMN IF NOT EXISTS effective_actor_input JSONB;
