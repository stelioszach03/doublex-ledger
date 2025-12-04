from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.domain.double_entry import Posting, post_journal_by_code


DECIMAL_6 = Decimal("0.000001")


def _upper_ccy(ccy: str) -> str:
    c = ccy.upper()
    if len(c) != 3:
        raise ValueError("Currency codes must be 3 letters")
    return c


def normalize_pair_str(pair: str) -> Tuple[str, str, str]:
    """
    Returns (pair_str, base_ccy, quote_ccy) normalized to 'AAA/BBB'.
    """
    parts = pair.replace("-", "/").split("/")
    if len(parts) != 2:
        raise ValueError("Pair must be in form 'AAA/BBB'")
    a, b = _upper_ccy(parts[0].strip()), _upper_ccy(parts[1].strip())
    if a == b:
        raise ValueError("Pair currencies cannot be equal")
    return f"{a}/{b}", a, b


def normalize_pair(a: str, b: str) -> Tuple[str, str, str]:
    return normalize_pair_str(f"{a}/{b}")


def store_fx_rate(
    db: Session,
    *,
    pair: str,
    rate: Decimal,
    valid_from: datetime,
    source: Optional[str] = None,
) -> models.FxRate:
    pair_n, base, quote = normalize_pair_str(pair)
    fx = models.FxRate(pair=pair_n, rate=rate, valid_from=valid_from, source=source)
    db.add(fx)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Try update existing on unique conflict
        existing = (
            db.execute(
                select(models.FxRate).where(
                    models.FxRate.pair == pair_n,
                    models.FxRate.valid_from == valid_from,
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            raise
        existing.rate = rate
        existing.source = source
        db.commit()
        fx = existing
    return fx


def get_rate_asof_pair(db: Session, *, pair: str, asof_ts: datetime) -> Decimal:
    pair_n, a, b = normalize_pair_str(pair)
    row = (
        db.execute(
            select(models.FxRate.rate)
            .where(models.FxRate.pair == pair_n, models.FxRate.valid_from <= asof_ts)
            .order_by(models.FxRate.valid_from.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        # Try reversed and invert
        rev = f"{b}/{a}"
        r = (
            db.execute(
                select(models.FxRate.rate)
                .where(models.FxRate.pair == rev, models.FxRate.valid_from <= asof_ts)
                .order_by(models.FxRate.valid_from.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if r is None:
            raise ValueError(f"No FX rate available for {pair_n} as of {asof_ts}")
        return (Decimal(1) / Decimal(r)).quantize(Decimal("0.0000000001"))
    return Decimal(row)


def get_rate_asof(db: Session, *, from_ccy: str, to_ccy: str, asof_ts: datetime) -> Decimal:
    from_ccy = _upper_ccy(from_ccy)
    to_ccy = _upper_ccy(to_ccy)
    if from_ccy == to_ccy:
        return Decimal(1)
    pair = f"{from_ccy}/{to_ccy}"
    return get_rate_asof_pair(db, pair=pair, asof_ts=asof_ts)


def convert(
    db: Session,
    *,
    amount: Decimal,
    from_ccy: str,
    to_ccy: str,
    asof_ts: datetime,
) -> Decimal:
    if from_ccy.upper() == to_ccy.upper():
        return amount
    rate = get_rate_asof(db, from_ccy=from_ccy, to_ccy=to_ccy, asof_ts=asof_ts)
    amt = (Decimal(amount) * Decimal(rate))
    return amt.quantize(DECIMAL_6, rounding=ROUND_HALF_UP)


def _ensure_account(db: Session, code: str, ccy: str) -> models.Account:
    acct = (
        db.execute(select(models.Account).where(models.Account.code == code)).scalars().first()
    )
    if not acct:
        raise ValueError(f"Account not found: {code}")
    if acct.ccy.upper() != ccy.upper():
        raise ValueError(
            f"Account {code} currency mismatch: {acct.ccy} != {ccy}"
        )
    if acct.status != models.AccountStatus.active:
        raise ValueError(f"Account inactive: {code}")
    return acct


def post_fx_settlement(
    db: Session,
    *,
    from_account_code: str,
    to_account_code: str,
    amount_from: Decimal,
    from_ccy: str,
    to_ccy: str,
    asof_ts: datetime,
    memo: str = "FX settlement",
    client_id: str = "fx",
    idempotency_key: Optional[str] = None,
    clearing_from_code: Optional[str] = None,
    clearing_to_code: Optional[str] = None,
) -> tuple[models.Journal, models.Journal] | tuple[models.Journal]:
    """
    Posts FX conversion using two same-currency journals bridged via clearing accounts:
    - Journal A (from_ccy): credit from_account, debit clearing_from.
    - Journal B (to_ccy): credit clearing_to, debit to_account.

    If from_ccy == to_ccy, a single journal is posted between accounts.
    """
    from_ccy = _upper_ccy(from_ccy)
    to_ccy = _upper_ccy(to_ccy)

    if from_ccy == to_ccy:
        j = post_journal_by_code(
            value_date=asof_ts.date(),
            entries=[
                Posting(account_code=to_account_code, amount=Decimal(amount_from), ccy=from_ccy, memo=memo),
                Posting(account_code=from_account_code, amount=-Decimal(amount_from), ccy=from_ccy, memo=memo),
            ],
            memo=memo,
            external_ref=f"FX:{client_id}:{idempotency_key or ''}:{asof_ts.isoformat()}",
            client_id=client_id,
            idempotency_key=f"{idempotency_key or asof_ts.isoformat()}::sameccy",
            db=db,
        )
        return (j,)

    # Ensure accounts exist and currencies match
    _ensure_account(db, from_account_code, from_ccy)
    _ensure_account(db, to_account_code, to_ccy)

    # Determine clearing accounts
    clearing_from_code = clearing_from_code or f"FX:CLEAR:{from_ccy}"
    clearing_to_code = clearing_to_code or f"FX:CLEAR:{to_ccy}"
    _ensure_account(db, clearing_from_code, from_ccy)
    _ensure_account(db, clearing_to_code, to_ccy)

    # Compute converted amount
    amount_to = convert(db, amount=Decimal(amount_from), from_ccy=from_ccy, to_ccy=to_ccy, asof_ts=asof_ts)

    # Journal A (from_ccy)
    j1 = post_journal_by_code(
        value_date=asof_ts.date(),
        entries=[
            Posting(account_code=clearing_from_code, amount=Decimal(amount_from), ccy=from_ccy, memo=memo),
            Posting(account_code=from_account_code, amount=-Decimal(amount_from), ccy=from_ccy, memo=memo),
        ],
        memo=memo,
        external_ref=f"FX-A:{client_id}:{idempotency_key or ''}:{asof_ts.isoformat()}",
        client_id=client_id,
        idempotency_key=f"{idempotency_key or asof_ts.isoformat()}::A",
        db=db,
    )

    # Journal B (to_ccy)
    j2 = post_journal_by_code(
        value_date=asof_ts.date(),
        entries=[
            Posting(account_code=to_account_code, amount=Decimal(amount_to), ccy=to_ccy, memo=memo),
            Posting(account_code=clearing_to_code, amount=-Decimal(amount_to), ccy=to_ccy, memo=memo),
        ],
        memo=memo,
        external_ref=f"FX-B:{client_id}:{idempotency_key or ''}:{asof_ts.isoformat()}",
        client_id=client_id,
        idempotency_key=f"{idempotency_key or asof_ts.isoformat()}::B",
        db=db,
    )

    return j1, j2

