from __future__ import annotations

from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from ledger.apps.api.deps import get_db
from ledger.db import models
from ledger.domain.double_entry import Posting, post_journal_by_code
from datetime import date


class PostingIn(BaseModel):
    account_code: str
    amount: Decimal
    ccy: str = Field(min_length=3, max_length=3)
    memo: Optional[str] = None


class JournalPostRequest(BaseModel):
    client_id: str
    idempotency_key: str
    value_date: Optional[date] = Field(default=None, description="YYYY-MM-DD")
    memo: Optional[str] = None
    entries: List[PostingIn]


class JournalPostResponse(BaseModel):
    journal_id: str
    status: str  # applied | duplicate
    created_at: str


router = APIRouter()


@router.post("/", response_model=JournalPostResponse, status_code=status.HTTP_201_CREATED)
def create_journal(payload: JournalPostRequest, db: Session = Depends(get_db)):
    if not payload.entries or len(payload.entries) < 2:
        raise HTTPException(status_code=400, detail="At least two entries required")

    # Pre-check duplicate via external_ref (best-effort)
    extref = f"API:{payload.client_id}:{payload.idempotency_key}"
    existing = (
        db.execute(
            select(models.Journal)
            .where(models.Journal.external_ref == extref)
            .order_by(models.Journal.created_at.desc())
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return JournalPostResponse(
            journal_id=str(existing.id),
            status="duplicate",
            created_at=existing.created_at.isoformat() if existing.created_at else "",
        )

    postings = [
        Posting(account_code=e.account_code, amount=e.amount, ccy=e.ccy.upper(), memo=e.memo)
        for e in payload.entries
    ]
    try:
        journal = post_journal_by_code(
            value_date=payload.value_date,
            entries=postings,
            memo=payload.memo or "",
            external_ref=extref,
            client_id=payload.client_id,
            idempotency_key=payload.idempotency_key,
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JournalPostResponse(
        journal_id=str(journal.id),
        status="applied",
        created_at=journal.created_at.isoformat() if journal.created_at else "",
    )
