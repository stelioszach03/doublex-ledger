from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.db.session import SessionLocal
from ledger.settings import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_cutoff() -> datetime:
    ttl_seconds = get_settings().idempotency_ttl_seconds
    return _now() - timedelta(seconds=ttl_seconds)


def ensure_idempotent(
    client_id: str, idempotency_key: str, db: Optional[Session] = None
) -> Optional[models.TransferRequest]:
    """
    Returns existing TransferRequest if found within TTL window; otherwise None.
    If an older (expired) row exists, it is deleted to allow re-use of the key.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        cutoff = _ttl_cutoff()
        with db.begin():
            tr = (
                db.execute(
                    select(models.TransferRequest).where(
                        models.TransferRequest.client_id == client_id,
                        models.TransferRequest.idempotency_key == idempotency_key,
                    )
                )
                .scalars()
                .first()
            )
            if tr is None:
                return None
            if tr.created_at is not None and tr.created_at >= cutoff:
                return tr
            # Expired -> delete and return None (allow re-use)
            db.delete(tr)
            return None
    finally:
        if close_db:
            db.close()


def record_idempotent_success(
    request_id: uuid.UUID, journal_id: uuid.UUID, db: Optional[Session] = None
) -> None:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        with db.begin():
            tr = db.get(models.TransferRequest, request_id)
            if tr is None:
                return
            tr.status = models.TransferStatus.applied
            tr.journal_id = journal_id
            # SQLAlchemy will flush on context exit
    finally:
        if close_db:
            db.close()


def record_idempotent_duplicate(
    request_id: uuid.UUID, db: Optional[Session] = None
) -> None:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        with db.begin():
            tr = db.get(models.TransferRequest, request_id)
            if tr is None:
                return
            tr.status = models.TransferStatus.duplicate
    finally:
        if close_db:
            db.close()

