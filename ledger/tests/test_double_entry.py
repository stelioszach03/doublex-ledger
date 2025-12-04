from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.domain.double_entry import (
    EntrySpec,
    validate_entries,
    post_journal_by_code,
    Posting,
    AccountInactiveError,
)


def test_validate_postings_balanced():
    a = uuid.uuid4()
    b = uuid.uuid4()
    entries = [
        EntrySpec(account_id=a, amount=Decimal("10.00"), ccy="USD"),
        EntrySpec(account_id=b, amount=Decimal("-10.00"), ccy="USD"),
    ]
    validate_entries(entries)  # no exception


def test_validate_postings_unbalanced():
    a = uuid.uuid4()
    b = uuid.uuid4()
    entries = [
        EntrySpec(account_id=a, amount=Decimal("10.00"), ccy="USD"),
        EntrySpec(account_id=b, amount=Decimal("-9.00"), ccy="USD"),
    ]
    with pytest.raises(ValueError):
        validate_entries(entries)


def _mk_account(db: Session, code: str, ccy: str = "EUR", status: models.AccountStatus = models.AccountStatus.active) -> models.Account:
    acc = models.Account(code=code, name=code, ccy=ccy, status=status)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def test_blocked_account_rejected(db: Session):
    _mk_account(db, "A1", ccy="EUR", status=models.AccountStatus.blocked)
    _mk_account(db, "A2", ccy="EUR", status=models.AccountStatus.active)
    with pytest.raises(AccountInactiveError):
        post_journal_by_code(
            value_date=date.today(),
            entries=[
                Posting(account_code="A1", amount=Decimal("10.00"), ccy="EUR"),
                Posting(account_code="A2", amount=Decimal("-10.00"), ccy="EUR"),
            ],
            memo="test",
            external_ref="T1",
            client_id="test",
            idempotency_key="k1",
            db=db,
        )


def test_idempotency_duplicate(db: Session):
    _mk_account(db, "A1")
    _mk_account(db, "A2")
    j1 = post_journal_by_code(
        value_date=date.today(),
        entries=[
            Posting(account_code="A1", amount=Decimal("10.00"), ccy="EUR"),
            Posting(account_code="A2", amount=Decimal("-10.00"), ccy="EUR"),
        ],
        memo="dup",
        external_ref="API:test:kdup",
        client_id="test",
        idempotency_key="kdup",
        db=db,
    )
    j2 = post_journal_by_code(
        value_date=date.today(),
        entries=[
            Posting(account_code="A1", amount=Decimal("10.00"), ccy="EUR"),
            Posting(account_code="A2", amount=Decimal("-10.00"), ccy="EUR"),
        ],
        memo="dup",
        external_ref="API:test:kdup",
        client_id="test",
        idempotency_key="kdup",
        db=db,
    )
    assert j2.id == j1.id


def test_serialization_retry_path(db: Session, monkeypatch):
    _mk_account(db, "A1")
    _mk_account(db, "A2")

    # Force first attempt to fail with a pseudo-serialization error
    from ledger.domain import double_entry as de

    calls = {"n": 0}

    class FauxError(Exception):
        pass

    def fake_lock(db_, ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FauxError("simulated serialization")
        return None

    def fake_is_ser(e: BaseException) -> bool:
        return isinstance(e, FauxError)

    monkeypatch.setattr(de, "_lock_accounts_xact", fake_lock)
    monkeypatch.setattr(de, "_is_serialization_failure", fake_is_ser)

    j = post_journal_by_code(
        value_date=date.today(),
        entries=[
            Posting(account_code="A1", amount=Decimal("10.00"), ccy="EUR"),
            Posting(account_code="A2", amount=Decimal("-10.00"), ccy="EUR"),
        ],
        memo="retry",
        external_ref="API:test:kretry",
        client_id="test",
        idempotency_key="kretry",
        db=db,
    )
    assert j is not None
    assert calls["n"] >= 2  # retried at least once
