from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy import text


def _hash_key(key: str) -> int:
    # Map arbitrary string to a SIGNED int64 for pg_advisory_[xact_]lock.
    # Taking 16 hex digits yields an UNSIGNED 64-bit integer (0..2^64-1) which
    # overflows Postgres' bigint range (-2^63..2^63-1) roughly half the time
    # (crashes with `bigint out of range`). Convert unsigned → signed by
    # subtracting 2^64 when the high bit is set.
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    u = int(h, 16)
    return u if u < (1 << 63) else u - (1 << 64)


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
