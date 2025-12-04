from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.db.session import SessionLocal
from ledger.settings import get_settings


class EodError(Exception):
    pass


def cut_off_window(cut_hour: int, cut_minute: int, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """
    Returns the (start, end) UTC timestamps of the current business day window
    based on a daily cut-off clock time (hour:minute).

    - If now <= today's cut-off, window is [yesterday cut-off, today cut-off].
    - If now > today's cut-off, window is [today cut-off, tomorrow cut-off].
    """
    now = now or datetime.now(timezone.utc)
    boundary_today = datetime.combine(now.date(), time(cut_hour, cut_minute, tzinfo=timezone.utc))
    if now > boundary_today:
        start = boundary_today
        end = boundary_today + timedelta(days=1)
    else:
        start = boundary_today - timedelta(days=1)
        end = boundary_today
    return start, end


def _end_of_day_ts(day: date, *, cut_hour: int, cut_minute: int) -> datetime:
    """
    End-of-day timestamp for the given business date using UTC timezone.
    The EOD boundary for `day` is exactly at day {cut_hour}:{cut_minute} UTC.
    """
    return datetime.combine(day, time(cut_hour, cut_minute, tzinfo=timezone.utc))


def _assert_no_unbalanced_journals(db: Session, for_date: date) -> None:
    # Defensive: DB trigger enforces per-journal balance at commit, but check anyway per spec.
    rows = (
        db.execute(
            select(models.Journal.id, func.abs(func.coalesce(func.sum(models.Entry.amount), 0)))
            .join(models.Entry, models.Entry.journal_id == models.Journal.id)
            .where(models.Journal.value_date == for_date)
            .group_by(models.Journal.id)
            .having(func.abs(func.coalesce(func.sum(models.Entry.amount), 0)) > Decimal("0.000001"))
        )
        .all()
    )
    if rows:
        jids = ", ".join(str(r[0]) for r in rows)
        raise EodError(f"Unbalanced journals exist for {for_date}: {jids}")


def _snapshot_balances(db: Session, as_of: datetime) -> int:
    count = 0
    accounts = db.execute(select(models.Account)).scalars().all()
    for acct in accounts:
        bal = (
            db.execute(
                select(func.coalesce(func.sum(models.Entry.amount), 0)).where(
                    models.Entry.account_id == acct.id,
                    models.Entry.created_at <= as_of,
                )
            )
            .scalars()
            .first()
        )
        snap = models.BalanceSnapshot(
            account_id=acct.id,
            as_of=as_of,
            balance=Decimal(bal or 0),
            ccy=acct.ccy,
        )
        db.add(snap)
        count += 1
    return count


def close_business_day(day: date, *, db: Optional[Session] = None, notes: Optional[str] = None) -> models.EodClose:
    """
    Performs end-of-day close for the given business date:
    - Verify no unbalanced journals exist for that value_date
    - Snapshot balances for all accounts into BalanceSnapshot at EOD boundary
    - Insert a row in EodClose to mark the day as closed
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        settings = get_settings()
        as_of = _end_of_day_ts(day, cut_hour=settings.cut_off_hour, cut_minute=settings.cut_off_minute)

        with db.begin():
            # Idempotency: don't double-close the same day
            existing = (
                db.execute(select(models.EodClose).where(models.EodClose.close_date == day)).scalars().first()
            )
            if existing is not None:
                logger.info("EOD close already exists for {}", day.isoformat())
                return existing

            _assert_no_unbalanced_journals(db, for_date=day)

            snaps = _snapshot_balances(db, as_of=as_of)
            logger.info("EOD balance snapshots created: {} for {}", snaps, day.isoformat())

            eod = models.EodClose(close_date=day, closed_at=datetime.now(timezone.utc), notes=notes)
            db.add(eod)
            # flush on context exit to get id if needed

        logger.info("EOD close executed for {} at {}", day.isoformat(), as_of.isoformat())
        # Reload managed instance
        return db.execute(select(models.EodClose).where(models.EodClose.close_date == day)).scalars().first()
    finally:
        if close_db:
            db.close()


# Backward compatibility with previous placeholder name
def close_day(day: date) -> None:
    close_business_day(day)
