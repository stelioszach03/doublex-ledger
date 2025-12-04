from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text as sa_text


def test_locking_contexts(monkeypatch, db: Session):
    # Reload locking to restore real functions (fixture patches to no-op)
    import importlib
    import ledger.domain.locking as locking
    importlib.reload(locking)

    # Patch text() to return a harmless SQL on SQLite
    monkeypatch.setattr(locking, "text", lambda s: sa_text("SELECT :k"))

    # Use contexts; they should not raise even on SQLite
    with locking.advisory_lock(db, "k1"):
        pass
    with locking.advisory_xact_lock(db, "k2"):
        pass
