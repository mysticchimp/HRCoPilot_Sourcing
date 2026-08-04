from collections.abc import Generator
from pathlib import Path
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger("sourcing.db")


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
    logger.info("Applying migration %s", path.name)
    conn.execute(text(path.read_text()))


def run_migrations() -> None:
    """Apply SQL migrations in order.

    001_init.sql runs only when `roles` is missing (CREATE TABLE).
    Later files are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
    and always applied so existing DBs pick up schema changes on boot.
    There is no schema_migrations ledger — re-running 002+ on every startup
    is intentional and safe because those files use IF NOT EXISTS.
    """
    if not MIGRATIONS_DIR.exists():
        logger.error(
            "Migrations directory missing: %s — schema changes will not apply",
            MIGRATIONS_DIR,
        )
        return

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    logger.info(
        "run_migrations start dir=%s files=%s",
        MIGRATIONS_DIR,
        [p.name for p in files],
    )

    with engine.begin() as conn:
        roles_exist = conn.execute(
            text("SELECT to_regclass('public.roles') IS NOT NULL")
        ).scalar()
        init = MIGRATIONS_DIR / "001_init.sql"
        if not roles_exist and init.exists():
            _apply_sql_file(conn, init)
        elif not roles_exist:
            logger.error("roles table missing and 001_init.sql not found")

        for path in files:
            if path.name == "001_init.sql":
                continue
            _apply_sql_file(conn, path)

    logger.info("run_migrations complete")
