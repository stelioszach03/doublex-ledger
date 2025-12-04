from __future__ import annotations

from datetime import date
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ledger.apps.api.deps import get_db
from ledger.db import models
from ledger.domain.double_entry import query_journals


class JournalOut(BaseModel):
    id: str
    external_ref: str | None

    class Config:
        from_attributes = True


router = APIRouter()


@router.get("/")
def list_journals(
    db: Session = Depends(get_db),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    account: str | None = None,
    external_ref: str | None = None,
):
    return query_journals(
        db,
        value_date_from=from_date,
        value_date_to=to_date,
        account_code=account,
        external_ref=external_ref,
    )


@router.get("/{journal_id}")
def get_journal(journal_id: uuid.UUID, db: Session = Depends(get_db)):
    journal = db.get(models.Journal, journal_id)
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")
    # Eager load entries
    _ = [e.id for e in journal.entries]
    return journal
