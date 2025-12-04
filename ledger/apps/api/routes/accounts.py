from __future__ import annotations

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ledger.apps.api.deps import get_db
from ledger.db import models
from ledger.domain.double_entry import get_account_balance
from datetime import datetime


class AccountCreate(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ccy: str = Field(min_length=3, max_length=3, description="ISO 4217")


class AccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    ccy: str
    status: str

    class Config:
        from_attributes = True


router = APIRouter()


@router.get("/", response_model=List[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(models.Account).order_by(models.Account.code).all()


@router.post("/", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(models.Account)
        .filter(models.Account.code == payload.code)
        .one_or_none()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Account code already exists")

    acct = models.Account(
        code=payload.code,
        name=payload.name,
        ccy=payload.ccy.upper(),
        status=models.AccountStatus.active,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


class BalanceOut(BaseModel):
    account_code: str
    balance: str
    as_of: datetime


@router.get("/{code}/balance", response_model=BalanceOut)
def account_balance(code: str, as_of: Optional[datetime] = None, db: Session = Depends(get_db)):
    acct = (
        db.query(models.Account)
        .filter(models.Account.code == code)
        .one_or_none()
    )
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    total = get_account_balance(db, account_code=code, at=as_of)
    return BalanceOut(account_code=code, balance=str(total), as_of=as_of or datetime.utcnow())
