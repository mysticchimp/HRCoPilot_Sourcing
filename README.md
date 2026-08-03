# Contra6 Sourcing (Apify)

Hosted, chat-driven LinkedIn pull service. Ports retrieval logic from
`Original Py Script/contra6_source2.py` — **no scoring**.

| Piece | Location |
|-------|----------|
| FastAPI + Postgres | `backend/` |
| SQL migration | `backend/migrations/001_init.sql` |
| Render (API + DB) | `render.yaml` |
| Dashboard UI | sibling `../Scoring_LLM` — **Sourcing** tab |

## Local run

```bash
# API
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # APIFY_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL
psql "$DATABASE_URL" -f migrations/001_init.sql
uvicorn app.main:app --reload --port 8000

# Dashboard (sibling)
cd ../Scoring_LLM && npm run dev
# Vite proxies /api → :8000; or set VITE_SOURCING_API_URL
```
