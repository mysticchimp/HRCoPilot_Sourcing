from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _apply_sql_file(conn, path: Path) -> None:
    conn.execute(text(path.read_text()))


def run_migrations() -> None:
    """Apply SQL migrations in order.

    001_init.sql runs only when `roles` is missing (CREATE TABLE).
    Later files are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
    and always applied so existing DBs pick up schema changes on boot.
    """
    if not MIGRATIONS_DIR.exists():
        return

    with engine.begin() as conn:
        roles_exist = conn.execute(
            text("SELECT to_regclass('public.roles') IS NOT NULL")
        ).scalar()
        init = MIGRATIONS_DIR / "001_init.sql"
        if not roles_exist and init.exists():
            _apply_sql_file(conn, init)

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name == "001_init.sql":
                continue
            _apply_sql_file(conn, path)
