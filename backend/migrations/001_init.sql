-- Contra6 Sourcing — initial schema
-- No scoring/rubric tables; this service stops at retrieve → dedupe → store.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    role_name   TEXT NOT NULL,
    client      TEXT,
    retrieval   JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_page   INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE candidates (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linkedin_url     TEXT NOT NULL UNIQUE,
    first_name       TEXT,
    last_name        TEXT,
    headline         TEXT,
    current_title    TEXT,
    current_company  TEXT,
    location         TEXT,
    top_skills       TEXT,
    raw_profile      JSONB,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pull_batches (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id          UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    batch_number     INT  NOT NULL,
    apify_run_id     TEXT,
    params_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pull_batches_role_id_idx ON pull_batches(role_id);
-- batch_number > 0 = enrichment pulls; <= 0 = probe audit rows
CREATE UNIQUE INDEX pull_batches_role_batch_uidx ON pull_batches(role_id, batch_number);

CREATE TABLE role_candidates (
    role_id       UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    candidate_id  UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    batch_id      UUID REFERENCES pull_batches(id) ON DELETE SET NULL,
    pulled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, candidate_id)
);

CREATE INDEX role_candidates_pulled_at_idx ON role_candidates(role_id, pulled_at DESC);

CREATE TABLE chat_sessions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id          UUID REFERENCES roles(id) ON DELETE SET NULL,
    state            TEXT NOT NULL DEFAULT 'intake'
                     CHECK (state IN ('intake', 'confirm', 'ready')),
    intake_progress  JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chat_sessions_role_id_idx ON chat_sessions(role_id);

CREATE TABLE chat_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chat_messages_session_id_idx ON chat_messages(session_id, created_at);
