from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ledger.apps.api.deps import get_db
from ledger.db import models
from ledger.apps.recon import reconcile as recon
from ledger.apps.recon import service as recon_service
from fastapi import HTTPException


router = APIRouter()


@router.get("/balances")
def balances(db: Session = Depends(get_db)):
    # Simple balances per account: debit - credit
    rows = (
        db.query(
            models.Account.id,
            models.Account.code,
            models.Account.name,
            models.Account.ccy,
            func.coalesce(func.sum(models.Entry.amount), 0).label("balance"),
        )
        .join(models.Entry, models.Entry.account_id == models.Account.id)
        .group_by(models.Account.id)
        .all()
    )
    out = []
    for row in rows:
        out.append(
            {
                "account_id": str(row[0]),
                "code": row[1],
                "name": row[2],
                "ccy": row[3],
                "balance": str(row[4] or Decimal(0)),
            }
        )
    return {"balances": out}


@router.get("/recon")
def recon_report(date: date):
    # Load precomputed summary written by CLI import-csv command
    summary = recon_service.load_summary_from_disk(date)
    if not summary:
        raise HTTPException(status_code=404, detail="Reconciliation report not found for date")
    return summary
