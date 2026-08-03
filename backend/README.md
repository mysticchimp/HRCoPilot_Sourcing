# Contra6 Sourcing API

Chat-driven LinkedIn sourcing via Apify. No scoring — retrieve, dedupe, store.

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill APIFY_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL
```

Apply schema (also auto-runs on boot if `roles` is missing):

```bash
psql "$DATABASE_URL" -f migrations/001_init.sql
```

```bash
uvicorn app.main:app --reload --port 8000
```

## Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/roles` | Role dropdown |
| POST | `/roles/{slug}/session` | Start/resume chat (`slug=new` for intake) |
| POST | `/chat/{role_slug}/message` | Intake / confirm / ready state machine |
| GET | `/roles/{slug}/candidates` | Results table |
| POST | `/roles/{slug}/pull` | Direct `pull_batch` |

## Deploy

`render.yaml` at repo root defines the Web Service + Postgres. Set `APIFY_TOKEN`,
`ANTHROPIC_API_KEY`, and `CORS_ORIGINS` (dashboard origin) in the Render dashboard.
