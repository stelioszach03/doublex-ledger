from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy import text


def _hash_key(key: str) -> int:
    # Map arbitrary string to int64 for advisory lock
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return int(h, 16)


@contextmanager
def advisory_lock(session: Session, key: str) -> Iterator[None]:
    conn: Connection = session.connection()
    lock_key = _hash_key(key)
    conn.execute(text("SELECT pg_advisory_lock(:k)").bindparams(k=lock_key))
    try:
        yield
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)").bindparams(k=lock_key))


@contextmanager
def advisory_xact_lock(session: Session, key: str) -> Iterator[None]:
    """
    Acquire an advisory lock bound to the current transaction.
    The lock is released automatically on COMMIT/ROLLBACK.
    """
    conn: Connection = session.connection()
    lock_key = _hash_key(key)
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=lock_key))
    try:
        yield
    finally:
        # Transaction-scoped locks auto-release; no explicit unlock.
        pass
