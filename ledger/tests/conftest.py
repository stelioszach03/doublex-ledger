from __future__ import annotations

import os
import types
from contextlib import contextmanager

import pytest

# Configure DB to use SQLite file before importing app modules
TEST_DB_PATH = os.path.abspath("test-ledger.db")
os.environ.setdefault("POSTGRES_DSN", f"sqlite+pysqlite:///{TEST_DB_PATH}")
os.environ.setdefault("APP_ENV", "dev")

from sqlalchemy.orm import Session

from ledger.db.session import engine, SessionLocal
from ledger.db import models


@pytest.fixture(autouse=True, scope="session")
def setup_db_session():
    # Create all tables once for the session
    models.Base.metadata.create_all(bind=engine)
    yield
    # Teardown
    try:
        models.Base.metadata.drop_all(bind=engine)
    except Exception:
        pass


@pytest.fixture()
def db() -> Session:
    s = SessionLocal()
    try:
        # Clean tables before each test (truncate order matters for FKs)
        for tbl in [
            models.Entry.__table__,
            models.Journal.__table__,
            models.TransferRequest.__table__,
            models.BalanceSnapshot.__table__,
            models.FxRate.__table__,
            models.EodClose.__table__,
            models.ReconException.__table__,
            models.Account.__table__,
        ]:
            s.execute(tbl.delete())
        s.commit()
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def patch_advisory_lock(monkeypatch):
    # No-op advisory locks for SQLite tests
    from ledger.domain import locking as _locking
    from ledger.domain import double_entry as _de

    @contextmanager
    def _noop(session, key):
        yield

    # Patch both the locking module and any references imported into double_entry
    monkeypatch.setattr(_locking, "advisory_xact_lock", _noop, raising=False)
    monkeypatch.setattr(_locking, "advisory_lock", _noop, raising=False)
    monkeypatch.setattr(_de, "advisory_xact_lock", _noop, raising=False)
    monkeypatch.setattr(_de, "advisory_lock", _noop, raising=False)
    yield


@pytest.fixture()
def client():
    # Import app after DB is configured
    from fastapi.testclient import TestClient
    from ledger.apps.api.main import app

    return TestClient(app)
