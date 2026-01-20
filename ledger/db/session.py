from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ledger.settings import get_settings


_settings = get_settings()

# Prefer POSTGRES_DSN; fallback to DATABASE_URL for compatibility
DATABASE_DSN = _settings.postgres_dsn or os.getenv(
    "DATABASE_URL", "postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
)

# Use a safer isolation level in production, but a more permissive one for
# development/tests so that concurrent sessions (used in tests) can observe
# each other's committed changes without needing to end the current transaction.
if DATABASE_DSN.startswith("sqlite"):
    # SQLite supports only UNCOMMITTED / SERIALIZABLE / AUTOCOMMIT
    _isolation = "SERIALIZABLE"
else:
    _isolation = "SERIALIZABLE" if _settings.app_env == "prod" else "READ COMMITTED"

engine = create_engine(
    DATABASE_DSN,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    isolation_level=_isolation,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
