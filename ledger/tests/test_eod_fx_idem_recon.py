from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.domain.double_entry import Posting, post_journal_by_code
from ledger.domain.eod import close_business_day, EodError
from ledger.domain.fx import (
    store_fx_rate,
    get_rate_asof_pair,
    get_rate_asof,
    convert,
    post_fx_settlement,
)
from ledger.domain.idempotency import (
    ensure_idempotent,
    record_idempotent_success,
    record_idempotent_duplicate,
)
from ledger.apps.api.observability.tracing import init_tracing
from ledger.apps.recon.service import import_csv_and_match


def _mk_account(db: Session, code: str, ccy: str = "EUR", status: models.AccountStatus = models.AccountStatus.active) -> models.Account:
    acc = models.Account(code=code, name=code, ccy=ccy, status=status)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def test_eod_close_and_snapshots(db: Session, monkeypatch):
    d = date.today()
    # Two accounts and one balanced journal
    _mk_account(db, "EOD-A", "EUR")
    _mk_account(db, "EOD-B", "EUR")
    post_journal_by_code(
        value_date=d,
        entries=[
            Posting(account_code="EOD-A", amount=Decimal("10.00"), ccy="EUR"),
            Posting(account_code="EOD-B", amount=Decimal("-10.00"), ccy="EUR"),
        ],
        memo="eod",
        external_ref="EOD:1",
        client_id="t",
        idempotency_key="eod1",
        db=db,
    )
    # Close day (idempotent)
    # Ensure clean transaction state for context-managed begin()
    try:
        db.rollback()
    except Exception:
        pass
    e1 = close_business_day(d, db=db)
    try:
        db.rollback()
    except Exception:
        pass
    e2 = close_business_day(d, db=db)
    assert e1.id == e2.id
    # Snapshots exist for both accounts
    snaps = db.query(models.BalanceSnapshot).filter(models.BalanceSnapshot.as_of.isnot(None)).all()
    assert len(snaps) >= 2


def test_eod_rejects_unbalanced(db: Session):
    d = date.today()
    a = _mk_account(db, "UB-A", "EUR")
    # Create unbalanced journal manually (single entry)
    j = models.Journal(value_date=d)
    db.add(j)
    db.flush()
    db.add(models.Entry(journal_id=j.id, account_id=a.id, amount=Decimal("5.00"), ccy="EUR"))
    # On Postgres, the DEFERRABLE constraint trigger fires on COMMIT and rejects unbalanced journals.
    # On SQLite (no trigger), the close_business_day check should raise EodError instead.
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
        # If commit succeeded (e.g., SQLite), closing should fail via domain check
        try:
            close_business_day(d, db=db)
            assert False, "Expected EodError"
        except EodError:
            pass
    except IntegrityError:
        db.rollback()
        # Expected on Postgres: unbalanced journal rejected by trigger
        pass


def test_fx_rates_and_conversion_and_settlement(db: Session):
    now = datetime.now(timezone.utc)
    # Rates
    store_fx_rate(db, pair="EUR/USD", rate=Decimal("1.2000000000"), valid_from=now)
    # Upsert path (same key)
    store_fx_rate(db, pair="EUR-USD", rate=Decimal("1.2500000000"), valid_from=now)
    r1 = get_rate_asof_pair(db, pair="EUR/USD", asof_ts=now)
    assert r1 == Decimal("1.2500000000")
    r2 = get_rate_asof(db, from_ccy="USD", to_ccy="EUR", asof_ts=now)
    # should be approx inverse of 1.25
    assert (Decimal(1) / Decimal("1.25") - r2).copy_abs() < Decimal("0.0000001")
    # convert
    amt = convert(db, amount=Decimal("10.00"), from_ccy="EUR", to_ccy="USD", asof_ts=now)
    assert amt == Decimal("12.500000")
    # same-ccy path
    assert get_rate_asof(db, from_ccy="EUR", to_ccy="EUR", asof_ts=now) == Decimal(1)
    # missing pair error
    import pytest
    with pytest.raises(ValueError):
        get_rate_asof_pair(db, pair="GBP/JPY", asof_ts=now)

    # Settlement journals require clearing accounts and endpoints
    _mk_account(db, "ACC:EUR", "EUR")
    _mk_account(db, "ACC:USD", "USD")
    _mk_account(db, "FX:CLEAR:EUR", "EUR")
    _mk_account(db, "FX:CLEAR:USD", "USD")
    j1, j2 = post_fx_settlement(
        db,
        from_account_code="ACC:EUR",
        to_account_code="ACC:USD",
        amount_from=Decimal("10.00"),
        from_ccy="EUR",
        to_ccy="USD",
        asof_ts=now,
        memo="fx",
        client_id="t",
        idempotency_key="fx1",
    )
    assert j1.id != j2.id


def test_idempotency_module(db: Session, monkeypatch):
    # Initially, ensure_idempotent returns None
    tr = ensure_idempotent("c1", "k1", db)
    assert tr is None
    # Insert request row
    req = models.TransferRequest(client_id="c1", idempotency_key="k1", status=models.TransferStatus.applied)
    db.add(req)
    db.commit()
    # Compute cutoffs based exactly on stored created_at to avoid tz mismatches across drivers
    import ledger.domain.idempotency as idem
    from sqlalchemy import select as _select
    from ledger.db.session import SessionLocal as _SL

    # Ensure a stored created_at exists (use DB default if needed)
    db.refresh(req)
    db.commit()

    # Load using a fresh Session to match how ensure_idempotent(None) will see tz info
    s2 = _SL()
    created2 = (
        s2.execute(
            _select(models.TransferRequest.created_at).where(
                models.TransferRequest.client_id == "c1",
                models.TransferRequest.idempotency_key == "k1",
            )
        )
        .scalars()
        .first()
    )
    s2.close()
    assert created2 is not None

    # Inside TTL: cutoff just before created_at (same tz semantics)
    inside_cutoff = created2 - timedelta(seconds=1)
    monkeypatch.setattr(idem, "_ttl_cutoff", lambda: inside_cutoff)
    tr2 = ensure_idempotent("c1", "k1", None)
    assert tr2 is not None

    # Expire: update created_at to older value, then set cutoff just after it
    older = (created2 - timedelta(days=2))
    req.created_at = older if older.tzinfo else older.replace(tzinfo=None)
    db.commit()
    s3 = _SL()
    created3 = (
        s3.execute(
            _select(models.TransferRequest.created_at).where(
                models.TransferRequest.client_id == "c1",
                models.TransferRequest.idempotency_key == "k1",
            )
        )
        .scalars()
        .first()
    )
    s3.close()
    assert created3 is not None
    expire_cutoff = created3 + timedelta(seconds=1)
    monkeypatch.setattr(idem, "_ttl_cutoff", lambda: expire_cutoff)
    tr3 = ensure_idempotent("c1", "k1", None)
    assert tr3 is None  # expired deleted
    # Record success / duplicate
    req2 = models.TransferRequest(client_id="c2", idempotency_key="k2", status=models.TransferStatus.applied)
    db.add(req2)
    db.commit()
    # Create a dummy journal
    j = models.Journal(external_ref="idem:ok")
    db.add(j)
    db.commit()
    record_idempotent_success(req2.id, j.id, None)
    db.refresh(req2)
    assert req2.journal_id == j.id
    record_idempotent_duplicate(req2.id, None)
    db.refresh(req2)
    assert req2.status == models.TransferStatus.duplicate


def test_tracing_init(monkeypatch):
    # Enable OTLP endpoint to exercise tracing init without network use
    import os

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
    # Patch HTTP post used by OTLP exporter to avoid real network
    import requests

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests.Session, "post", lambda self, url, data=None, json=None, **kw: _Resp())
    app = FastAPI()
    init_tracing(app)  # should not raise
    # Shutdown provider and reset to avoid lingering background threads
    from opentelemetry import trace as _trace
    try:
        _trace.get_tracer_provider().shutdown()  # type: ignore[attr-defined]
    except Exception:
        pass
    from opentelemetry.sdk.trace import TracerProvider as _TP
    _trace.set_tracer_provider(_TP())


def test_recon_service_import_csv_and_match(db: Session, tmp_path):
    d = date.today()
    # Create accounts and balanced journals to match against
    a1 = _mk_account(db, "RCN-A", "EUR")
    a2 = _mk_account(db, "RCN-B", "EUR")
    j_exact = post_journal_by_code(
        value_date=d,
        entries=[
            Posting(account_code="RCN-A", amount=Decimal("25.00"), ccy="EUR"),
            Posting(account_code="RCN-B", amount=Decimal("-25.00"), ccy="EUR"),
        ],
        memo="rcn-exact",
        external_ref="EXT-REF-1",
        client_id="rcn",
        idempotency_key="rcn1",
        db=db,
    )
    j_amount = post_journal_by_code(
        value_date=d,
        entries=[
            Posting(account_code="RCN-A", amount=Decimal("55.00"), ccy="EUR"),
            Posting(account_code="RCN-B", amount=Decimal("-55.00"), ccy="EUR"),
        ],
        memo="rcn-amt",
        external_ref="",
        client_id="rcn",
        idempotency_key="rcn2",
        db=db,
    )

    # Prepare external CSV (generic mapping)
    csv_path = tmp_path / "ext.csv"
    csv_path.write_text("external_ref,amount,ccy\nEXT-REF-1,25,EUR\n,55,EUR\nUNMATCH,7,EUR\n")

    # Ensure we are not inside an implicit transaction before entering begin()
    try:
        db.rollback()
    except Exception:
        pass
    summary = import_csv_and_match(recon_date=d, path=str(csv_path), mapping="generic", default_ccy="EUR", db=None)
    # Expect 2 matches and 1 unmatched external
    assert summary.matched_count >= 2
    assert summary.unmatched_external_count >= 1
    # Check a ReconException row inserted
    ex_rows = db.query(models.ReconException).filter(models.ReconException.recon_date == d).all()
    assert len(ex_rows) >= 1
    # Write HTML and reload summaries from disk
    from ledger.apps.recon.service import save_html_report, load_summary_from_disk, load_full_summary_from_disk
    p = save_html_report(summary)
    assert p.exists()
    js = load_summary_from_disk(d)
    assert js is not None and js.get("date") == d.isoformat()
    full = load_full_summary_from_disk(d)
    assert full is not None and full.recon_date == d


def test_eod_cutoff_window_and_close_wrapper(db: Session):
    from ledger.domain.eod import cut_off_window, close_day
    # At 10:00 with cutoff 23:59, window should be [prev day cutoff, today cutoff]
    fake_now = datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
    start, end = cut_off_window(23, 59, now=fake_now)
    assert start.date() == date(2025, 1, 1)
    assert end.date() == date(2025, 1, 2)
    # close_day delegates to close_business_day without raising
    close_day(date.today())


def test_fx_pair_normalization_errors():
    from ledger.domain.fx import normalize_pair_str
    import pytest
    with pytest.raises(ValueError):
        normalize_pair_str("EUR")
    with pytest.raises(ValueError):
        normalize_pair_str("EUR/EUR")
