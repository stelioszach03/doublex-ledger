from __future__ import annotations

import os

from fastapi.testclient import TestClient


def test_main_lifespan_and_logging(monkeypatch):
    # Exercise logging config branches
    os.environ["LOG_FORMAT"] = "json"
    from ledger.apps.api import main as api_main

    api_main._configure_logging()
    os.environ["LOG_FORMAT"] = "plain"
    api_main._configure_logging()

    # Exercise DB healthcheck
    api_main._db_healthcheck()

    # Force auto-migrate fallback path (alembic upgrade raises)
    os.environ["MIGRATE_ON_START"] = "true"
    import alembic.command as ac

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ac, "upgrade", boom)
    api_main._auto_migrate()

    # Create TestClient to ensure lifespan runs without error
    client = TestClient(api_main.app)
    r = client.get("/health")
    assert r.status_code == 200
    # Middleware exception branch: make metrics labels throw
    from ledger.apps.api import main as m
    class Boom(Exception):
        pass
    class Fake:
        def labels(self, **kw):
            raise Boom()
    monkeypatch.setattr(m, "REQUEST_COUNT", Fake())
    r = client.get("/health")
    assert r.status_code == 200
    # Early-return branch of _auto_migrate
    os.environ["MIGRATE_ON_START"] = "false"
    api_main._auto_migrate()
