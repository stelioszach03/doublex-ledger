from __future__ import annotations

from datetime import date
from decimal import Decimal


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    r2 = client.get("/api/health")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"


def test_accounts_and_balances(client):
    # Create accounts
    a1 = {"code": "ACC-1001", "name": "Cash", "ccy": "EUR"}
    a2 = {"code": "ACC-2001", "name": "AR", "ccy": "EUR"}
    r = client.post("/api/accounts", json=a1)
    assert r.status_code in (200, 201, 409)
    r = client.post("/api/accounts", json=a2)
    assert r.status_code in (200, 201, 409)

    # List accounts
    r = client.get("/api/accounts")
    assert r.status_code == 200
    # Balance endpoint
    r = client.get(f"/api/accounts/{a1['code']}/balance")
    assert r.status_code == 200
    assert Decimal(r.json()["balance"]) >= 0
    # Not found balance
    r = client.get("/api/accounts/NOPE/balance")
    assert r.status_code == 404


def test_transfers_applied_and_duplicate(client):
    # Ensure accounts
    for code in ("ACC-T1", "ACC-T2"):
        client.post("/api/accounts", json={"code": code, "name": code, "ccy": "EUR"})

    payload = {
        "client_id": "test",
        "idempotency_key": "idem-1",
        "value_date": date.today().isoformat(),
        "memo": "test",
        "entries": [
            {"account_code": "ACC-T1", "amount": "100.00", "ccy": "EUR"},
            {"account_code": "ACC-T2", "amount": "-100.00", "ccy": "EUR"},
        ],
    }
    r1 = client.post("/api/transfers", json=payload)
    assert r1.status_code == 201
    assert r1.json()["status"] == "applied"
    r2 = client.post("/api/transfers", json=payload)
    assert r2.status_code == 201
    assert r2.json()["status"] == "duplicate"
    # Get journal by id
    jid = r1.json()["journal_id"]
    r = client.get(f"/api/journals/{jid}")
    assert r.status_code == 200
    # 404 journal
    r = client.get("/api/journals/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    # Bad payload (one entry)
    bad = dict(payload)
    bad["entries"] = [
        {"account_code": "ACC-T1", "amount": "10.00", "ccy": "EUR"}
    ]
    r = client.post("/api/transfers", json=bad)
    assert r.status_code == 400
    # Mixed currencies -> 400 via exception path
    bad2 = dict(payload)
    bad2["idempotency_key"] = "idem-2"
    bad2["entries"] = [
        {"account_code": "ACC-T1", "amount": "10.00", "ccy": "EUR"},
        {"account_code": "ACC-T2", "amount": "-10.00", "ccy": "USD"},
    ]
    r = client.post("/api/transfers", json=bad2)
    assert r.status_code == 400


def test_journals_filters(client):
    # At least one journal exists from previous test
    q = {
        "from": date.today().isoformat(),
        "to": date.today().isoformat(),
        "account": "ACC-T1",
    }
    r = client.get("/api/journals", params=q)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    # balances report
    r = client.get("/api/reports/balances")
    assert r.status_code == 200


def test_recon_report_smoke(client, tmp_path):
    # Write a minimal recon JSON summary that API should serve
    from ledger.apps.recon.service import _ensure_reports_dir
    out_dir = _ensure_reports_dir()
    d = date.today().isoformat()
    (out_dir / f"recon-{d}.json").write_text('{"matched_count":0,"unmatched_external_count":0,"unmatched_ledger_count":0,"date":"%s"}' % d)

    r = client.get(f"/api/reports/recon", params={"date": d})
    assert r.status_code == 200
    data = r.json()
    assert data["date"] == d
    # 404 path
    r = client.get(f"/api/reports/recon", params={"date": "1999-01-01"})
    assert r.status_code == 404
    # ready + metrics
    r = client.get("/ready")
    assert r.status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 200
