from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional, Sequence
import random
import time
from datetime import date, datetime

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ledger.db import models
from ledger.db.session import SessionLocal
from ledger.domain.locking import advisory_lock, advisory_xact_lock
from opentelemetry import trace
from ledger.apps.api.observability.metrics import (
    LEDGER_TX_TOTAL,
    LEDGER_POSTING_LATENCY_MS,
    LEDGER_SERIALIZATION_RETRIES_TOTAL,
)
from time import perf_counter


@dataclass(frozen=True)
class EntrySpec:
    account_id: uuid.UUID
    amount: Decimal  # debit positive, credit negative
    ccy: str
    memo: str | None = None


# Posting by account code (for API/MVP convenience)
@dataclass(frozen=True)
class Posting:
    account_code: str
    amount: Decimal
    ccy: str
    memo: str | None = None


class DomainError(Exception):
    pass


class UnbalancedJournalError(DomainError):
    pass


class MixedCurrencyError(DomainError):
    pass


class AccountNotFoundError(DomainError):
    pass


class AccountInactiveError(DomainError):
    pass


def validate_entries(entries: Iterable[EntrySpec]) -> None:
    total = Decimal(0)
    ccy: Optional[str] = None
    for e in entries:
        if e.amount == 0:
            raise ValueError("Entry amount cannot be zero")
        if ccy is None:
            ccy = e.ccy
        elif ccy != e.ccy:
            raise ValueError("Mixed currencies in entries not allowed for MVP")
        total += e.amount
    if total != 0:
        raise ValueError("Entries are not balanced")


def post_journal(
    db: Session,
    description: str,
    entries: List[EntrySpec],
    external_ref: str | None = None,
) -> uuid.UUID:
    tracer = trace.get_tracer(__name__)
    t0 = perf_counter()
    with tracer.start_as_current_span("post_journal"):
        try:
            validate_entries(entries)

            journal = models.Journal(external_ref=external_ref)
            db.add(journal)
            db.flush()

            # Acquire advisory locks on accounts to avoid race conditions
            account_ids = sorted({str(e.account_id) for e in entries})
            with _multi_lock(db, account_ids):
                for e in entries:
                    db.add(
                        models.Entry(
                            journal_id=journal.id,
                            account_id=e.account_id,
                            amount=e.amount,
                            ccy=e.ccy,
                            memo=e.memo,
                        )
                    )
                db.commit()
            LEDGER_TX_TOTAL.labels(result="ok").inc()
            return journal.id
        except Exception:
            LEDGER_TX_TOTAL.labels(result="fail").inc()
            raise
        finally:
            dt_ms = (perf_counter() - t0) * 1000.0
            LEDGER_POSTING_LATENCY_MS.observe(dt_ms)


def _is_serialization_failure(err: BaseException) -> bool:
    if isinstance(err, DBAPIError) and getattr(err, "orig", None) is not None:
        sqlstate = getattr(err.orig, "sqlstate", None) or getattr(err.orig, "pgcode", None)
        return sqlstate == "40001"
    return False


def _lock_accounts_xact(db: Session, account_ids: Sequence[uuid.UUID]) -> None:
    # Sort deterministically to avoid deadlocks
    for aid in sorted([str(a) for a in account_ids]):
        # transaction-scoped advisory lock on each account
        with advisory_xact_lock(db, f"account:{aid}"):
            # Acquire-and-release pattern to ensure ordering; keep lock held
            pass


def _load_accounts_by_code(db: Session, codes: Sequence[str]) -> dict[str, models.Account]:
    rows: list[models.Account] = (
        db.execute(select(models.Account).where(models.Account.code.in_(list(set(codes))))).scalars().all()
    )
    return {a.code: a for a in rows}


def post_journal_by_code(
    *,
    value_date: Optional[datetime] | Optional[date],
    entries: List[Posting],
    memo: str,
    external_ref: str | None,
    client_id: str,
    idempotency_key: str,
    db: Session | None = None,
) -> models.Journal:
    """
    Core posting with idempotency and SERIALIZABLE + retry.

    - Validates balanced entries and single currency (MVP)
    - Ensures accounts exist and are active
    - Acquires pg_advisory_xact_lock on all touched accounts (sorted)
    - Inserts a TransferRequest row (idempotency); on UNIQUE conflict, load original Journal
    - Creates Journal + Entry rows and returns the Journal

    If mixed currencies are provided, raise MixedCurrencyError suggesting FX settlement.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    tracer = trace.get_tracer(__name__)
    t0 = perf_counter()
    try:
        # Normalize + validate inputs outside of transaction
        if not entries:
            raise UnbalancedJournalError("No entries provided")

        # Validate balanced and single currency
        total = Decimal(0)
        ccys = set()
        for p in entries:
            if p.amount == 0:
                raise UnbalancedJournalError("Entry amount cannot be zero")
            total += p.amount
            ccys.add(p.ccy.upper())
        if total != 0:
            raise UnbalancedJournalError("Entries are not balanced")
        if len(ccys) != 1:
            raise MixedCurrencyError("Mixed currencies not allowed. Use fx.post_fx_settlement(...) first.")
        ccy = next(iter(ccys))

        # Load and check accounts
        codes = [p.account_code for p in entries]
        acc_map = _load_accounts_by_code(db, codes)
        missing = [c for c in codes if c not in acc_map]
        if missing:
            raise AccountNotFoundError(f"Accounts not found: {', '.join(sorted(set(missing)))}")
        inactive = [a.code for a in acc_map.values() if a.status != models.AccountStatus.active]
        if inactive:
            raise AccountInactiveError(f"Accounts inactive/blocked: {', '.join(sorted(inactive))}")

        # Enforce EOD closed-date cutoff (forbid backdated postings before latest closed date)
        vdate: Optional[date] = None
        if isinstance(value_date, datetime):
            vdate = value_date.date()
        elif isinstance(value_date, date):
            vdate = value_date
        if vdate is not None:
            latest_closed: Optional[date] = (
                db.execute(select(func.max(models.EodClose.close_date))).scalars().first()
            )
            if latest_closed is not None and vdate < latest_closed:
                raise DomainError(
                    f"Cannot post with value_date={vdate}: latest closed date is {latest_closed}"
                )

        # Ensure no implicit transaction is open before starting managed one
        try:
            db.rollback()
        except Exception:
            pass

        # Retry loop for SERIALIZABLE
        req_key = f"{client_id}:{idempotency_key}"
        max_retries = 5
        base_delay = 0.05
        for attempt in range(1, max_retries + 1):
            try:
                with tracer.start_as_current_span("post_journal"):
                    with db.begin():
                        # Idempotency insert (first to gate concurrent duplicates)
                        tr = models.TransferRequest(
                            client_id=client_id,
                            idempotency_key=idempotency_key,
                            status=models.TransferStatus.applied,
                        )
                        db.add(tr)
                        db.flush()

                        # Acquire transaction-scoped advisory locks
                        account_ids = [acc_map[p.account_code].id for p in entries]
                        _lock_accounts_xact(db, account_ids)

                        # Create journal
                        journal = models.Journal(
                            value_date=value_date if isinstance(value_date, date) else None,
                            external_ref=external_ref or req_key,
                        )
                        db.add(journal)
                        db.flush()

                        # Insert entries (each with per-line memo or fallback to journal memo)
                        for p in entries:
                            acc = acc_map[p.account_code]
                            db.add(
                                models.Entry(
                                    journal_id=journal.id,
                                    account_id=acc.id,
                                    amount=p.amount,
                                    ccy=ccy,
                                    memo=p.memo or memo,
                                )
                            )

                        # Commit happens on context exit
                    # Success
                    # Reload and return a managed instance
                    LEDGER_TX_TOTAL.labels(result="ok").inc()
                    return db.get(models.Journal, journal.id)
            except IntegrityError as e:
                # Rollback any failed transaction state before proceeding
                try:
                    db.rollback()
                except Exception:
                    pass
                # Attempt to load the already-created journal by external_ref regardless of backend.
                existing = (
                    db.execute(
                        select(models.Journal)
                        .where(models.Journal.external_ref == (external_ref or req_key))
                        .order_by(models.Journal.created_at.desc())
                    )
                    .scalars()
                    .first()
                )
                if existing is not None:
                    return existing
                # Not found yet => fall through to retry path (could be mid-commit or other integrity issue)
                if attempt >= max_retries:
                    raise
            except Exception as e:
                # Ensure clean state for next attempt
                try:
                    db.rollback()
                except Exception:
                    pass
                if _is_serialization_failure(e) and attempt < max_retries:
                    LEDGER_SERIALIZATION_RETRIES_TOTAL.inc()
                    delay = base_delay * (2 ** (attempt - 1)) * (1 + random.random())
                    time.sleep(delay)
                    continue
                raise
        # Exhausted retries
        raise DomainError("Could not post journal due to concurrent activity (retries exhausted)")
    finally:
        dt_ms = (perf_counter() - t0) * 1000.0
        try:
            LEDGER_POSTING_LATENCY_MS.observe(dt_ms)
        finally:
            if close_db:
                db.close()


def post_transfer(
    db: Session,
    from_account_id: uuid.UUID,
    to_account_id: uuid.UUID,
    amount: Decimal,
    ccy: str,
    description: str,
    external_ref: str | None = None,
) -> uuid.UUID:
    entries = [
        EntrySpec(account_id=from_account_id, amount=-amount, ccy=ccy, memo=description),
        EntrySpec(account_id=to_account_id, amount=amount, ccy=ccy, memo=description),
    ]
    return post_journal(db=db, description=description, entries=entries, external_ref=external_ref)


class _multi_lock:
    def __init__(self, db: Session, keys: Iterable[str]):
        self.db = db
        self.keys = list(keys)
        self._ctx = []

    def __enter__(self):
        for k in self.keys:
            ctx = advisory_xact_lock(self.db, f"account:{k}")
            self._ctx.append(ctx)
            ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        for ctx in reversed(self._ctx):
            ctx.__exit__(exc_type, exc, tb)
        self._ctx.clear()


def get_account_balance(db: Session, account_code: str, at: Optional[datetime] = None) -> Decimal:
    """
    Balance = SUM(entries.amount) up to timestamp `at` (by entry.created_at).
    If `at` is None, uses now().
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("get_balance"):
        acct = db.execute(select(models.Account).where(models.Account.code == account_code)).scalars().first()
        if not acct:
            raise AccountNotFoundError(f"Account not found: {account_code}")

        cutoff_expr = func.now() if at is None else at
        total = (
            db.execute(
                select(func.coalesce(func.sum(models.Entry.amount), 0)).where(
                    models.Entry.account_id == acct.id,
                    models.Entry.created_at <= cutoff_expr,
                )
            )
            .scalars()
            .first()
        )
        return Decimal(total or 0)


def query_journals(
    db: Session,
    *,
    value_date_from: Optional[date] = None,
    value_date_to: Optional[date] = None,
    external_ref: Optional[str] = None,
    account_code: Optional[str] = None,
) -> list[models.Journal]:
    q = select(models.Journal)
    if value_date_from is not None:
        q = q.where(models.Journal.value_date >= value_date_from)
    if value_date_to is not None:
        q = q.where(models.Journal.value_date <= value_date_to)
    if external_ref is not None:
        q = q.where(models.Journal.external_ref == external_ref)
    if account_code is not None:
        sub = (
            select(models.Entry.journal_id)
            .join(models.Account, models.Account.id == models.Entry.account_id)
            .where(models.Account.code == account_code)
            .distinct()
        )
        q = q.where(models.Journal.id.in_(sub))
    q = q.order_by(models.Journal.created_at.desc())
    return db.execute(q).scalars().all()
