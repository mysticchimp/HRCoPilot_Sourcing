from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Apply 001_init.sql if tables are missing (idempotent CREATE IF NOT EXISTS)."""
    import pathlib

    migration = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
    if not migration.exists():
        return
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT to_regclass('public.roles') IS NOT NULL")
        ).scalar()
        if not exists:
            conn.execute(text(migration.read_text()))
