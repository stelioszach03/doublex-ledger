from __future__ import annotations

from typing import Generator

from ledger.db.session import SessionLocal
from ledger.settings import Settings, get_settings


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_app_settings() -> Settings:
    return get_settings()
