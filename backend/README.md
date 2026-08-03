# Contra6 Sourcing API

Chat-driven LinkedIn sourcing via Apify. No scoring — retrieve, dedupe, store.

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill APIFY_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL
```

Schema applies on boot (`001_init.sql` + `002_archive_and_role_name.sql`). Login is
dummy — any non-empty email/password works; nothing is stored.

```bash
uvicorn app.main:app --reload --port 8000
```

## Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/login` | Dummy email/password → httpOnly JWT cookie |
| POST | `/auth/logout` | Clear cookie |
| GET | `/auth/me` | Current session user |
| GET | `/roles` | Active roles (`?include_archived=true` for all) |
| GET | `/roles/archived` | Archived roles |
| POST | `/roles/{slug}/archive` | Soft-archive |
| POST | `/roles/{slug}/unarchive` | Restore |
| POST | `/roles/{slug}/session` | Start/resume chat (`slug=new` for intake) |
| POST | `/chat/{role_slug}/message` | Intake / confirm / ready state machine |
| GET | `/roles/{slug}/candidates` | Results table |
| POST | `/roles/{slug}/pull` | Direct `pull_batch` |

## Deploy

`render.yaml` at repo root defines the Web Service + Postgres. Set `APIFY_TOKEN`,
`ANTHROPIC_API_KEY`, and `CORS_ORIGINS` (dashboard origin) in the Render dashboard.
