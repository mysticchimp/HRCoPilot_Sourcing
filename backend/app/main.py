"""Contra6 Sourcing API — FastAPI entrypoint for Render Web Service."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import run_migrations
from app.routers import auth as auth_router
from app.routers.sourcing import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("sourcing.chat").setLevel(logging.INFO)
logging.getLogger("sourcing.pull").setLevel(logging.INFO)
logging.getLogger("sourcing.apify").setLevel(logging.INFO)
logging.getLogger("sourcing.scoring").setLevel(logging.INFO)
logging.getLogger("sourcing.db").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield


settings = get_settings()
app = FastAPI(title="Contra6 Sourcing", version="1.0.0", lifespan=lifespan)

# Vercel previews/production are https://*.vercel.app — always allow those in
# addition to explicit CORS_ORIGINS (localhost, custom domains, etc.).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["http://localhost:5173"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(router)


@app.get("/")
def root():
    return {"service": "contra6-sourcing", "docs": "/docs"}
