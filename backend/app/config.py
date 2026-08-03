import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        raw = os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/sourcing",
        )
        # Render sometimes provides postgres:// — normalize scheme
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://"):]
        # Prefer psycopg (v3) driver
        if raw.startswith("postgresql://") and "+psycopg" not in raw:
            raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
        self.database_url = raw

        self.apify_token = os.environ.get("APIFY_TOKEN", "")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.cors_origins = [
            o.strip()
            for o in os.environ.get(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if o.strip()
        ]
        self.query_model = os.environ.get("QUERY_MODEL", "claude-haiku-4-5")
        self.default_batch_size = int(os.environ.get("DEFAULT_BATCH_SIZE", "25"))
        self.jwt_secret = os.environ.get(
            "JWT_SECRET",
            "dev-only-change-me-in-production",
        )
        self.jwt_expire_hours = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))
        self.cookie_secure = os.environ.get("COOKIE_SECURE", "").lower() in (
            "1",
            "true",
            "yes",
        )
        # Dummy login accounts (email/password/role). Nothing stored in the DB.
        self.dummy_accounts = [
            {
                "email": os.environ.get("DUMMY_ADMIN_EMAIL", "admin@contra6.com").strip().lower(),
                "password": os.environ.get("DUMMY_ADMIN_PASSWORD", "admin"),
                "role": "admin",
            },
            {
                "email": os.environ.get("DUMMY_HR_EMAIL", "hr@contra6.com").strip().lower(),
                "password": os.environ.get("DUMMY_HR_PASSWORD", "hr"),
                "role": "hr_manager",
            },
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
