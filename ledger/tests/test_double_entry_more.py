from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from ledger.db import models
from ledger.domain.double_entry import (
    EntrySpec,
    validate_entries,
    AccountNotFoundError,
    post_journal,
    get_account_balance,
    query_journals,
    post_transfer,
    _is_serialization_failure,
)
from ledger.domain.fx import post_fx_settlement
from ledger.domain.locking import _hash_key


def _mk_account(db: Session, code: str, ccy: str = "EUR") -> models.Account:
    a = models.Account(code=code, name=code, ccy=ccy, status=models.AccountStatus.active)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_post_journal_and_balance_and_query(db: Session):
    a1 = _mk_account(db, "M-A1")
    a2 = _mk_account(db, "M-A2")
    jid = post_journal(
        db=db,
        description="manual",
        entries=[
            EntrySpec(account_id=a1.id, amount=Decimal("5.00"), ccy="EUR"),
            EntrySpec(account_id=a2.id, amount=Decimal("-5.00"), ccy="EUR"),
        ],
        external_ref="MANUAL:1",
    )
    assert isinstance(jid, uuid.UUID)
    bal1 = get_account_balance(db, "M-A1")
    bal2 = get_account_balance(db, "M-A2")
    assert bal1 == Decimal("5.00") and bal2 == Decimal("-5.00")
    js = query_journals(db, external_ref="MANUAL:1", account_code="M-A1")
    assert len(js) == 1 and str(js[0].id) == str(jid)
    # validate_entries error paths
    try:
        validate_entries([EntrySpec(account_id=a1.id, amount=Decimal("0"), ccy="EUR")])
        assert False
    except ValueError:
        pass
    try:
        validate_entries([
            EntrySpec(account_id=a1.id, amount=Decimal("1.00"), ccy="EUR"),
            EntrySpec(account_id=a2.id, amount=Decimal("-1.00"), ccy="USD"),
        ])
        assert False
    except ValueError:
        pass
    # post_transfer wrapper
    jid2 = post_transfer(db, a1.id, a2.id, Decimal("1.00"), "EUR", "desc")
    assert isinstance(jid2, uuid.UUID)
    # get_account_balance not found
    import pytest
    with pytest.raises(AccountNotFoundError):
        get_account_balance(db, "NOPE")
    # _is_serialization_failure branches
    class Orig:
        sqlstate = "40001"
    from sqlalchemy.exc import DBAPIError
    e = DBAPIError("stmt", {}, Orig())
    assert _is_serialization_failure(e) is True
    assert _is_serialization_failure(Exception("x")) is False


def test_post_journal_exception_path(db: Session):
    a1 = _mk_account(db, "M-B1")
    # zero amount to trigger validate error inside post_journal
    try:
        post_journal(
            db,
            description="bad",
            entries=[EntrySpec(account_id=a1.id, amount=Decimal("0"), ccy="EUR")],
            external_ref="BAD",
        )
        assert False
    except ValueError:
        pass


def test_fx_same_currency_single_journal(db: Session):
    _mk_account(db, "FX-A", "EUR")
    _mk_account(db, "FX-B", "EUR")
    now = datetime.now(timezone.utc)
    journals = post_fx_settlement(
        db,
        from_account_code="FX-A",
        to_account_code="FX-B",
        amount_from=Decimal("3.00"),
        from_ccy="EUR",
        to_ccy="EUR",
        asof_ts=now,
        client_id="t",
        idempotency_key="same",
    )
    assert len(journals) == 1


def test_hash_key_deterministic():
    k1 = _hash_key("abc")
    k2 = _hash_key("abc")
    assert isinstance(k1, int) and k1 == k2
