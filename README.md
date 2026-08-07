# DoubleX Ledger

Double-entry accounting ledger on PostgreSQL: SERIALIZABLE isolation, idempotent postings, trigger-enforced balance, FX settlement through a clearing account, and a reconciliation CLI.

[![CI](https://github.com/stelioszach03/doublex-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/stelioszach03/doublex-ledger/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-f59e0b?style=flat-square)](LICENSE)

## Results

Every invariant is enforced in the database, not in application code, so it cannot be skipped by a caller.

| Property | How it is enforced | Evidence |
|---|---|---|
| Entries sum to zero per currency | Postgres trigger on `postings` | [`ledger/db/`](ledger/db/) migrations |
| Idempotent writes under retry | unique index on `(client_id, idempotency_key)`; replay returns `status=duplicate` with the original `journal_id` | [`ledger/domain/idempotency.py`](ledger/domain/idempotency.py) |
| Serializable reads and writes | `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE`, retry on `40001` | [`ledger/domain/double_entry.py`](ledger/domain/double_entry.py) |
| Deterministic advisory locks | SHA-256 prefix mapped into signed int64, so a hash collision cannot raise `bigint out of range` | [`ledger/domain/locking.py`](ledger/domain/locking.py) |
| No posting into a closed day | `eod.check_closed_before(value_date)` | [`ledger/domain/eod.py`](ledger/domain/eod.py) |
| Balanced-posting invariants | Hypothesis property tests | [`ledger/tests/property/test_postings_property.py`](ledger/tests/property/test_postings_property.py) |

**No coverage percentage and no throughput number are claimed here.** CI runs on Python 3.11 and 3.12 against a live Postgres service and uploads `coverage.xml` and `htmlcov` as build artifacts, but there is no coverage gate and no report committed to this repo — download the artifact from a CI run to see the current figure.

## Run

```bash
cp .env.example .env
make compose-up      # API :8000 · Prometheus :9090 · Grafana :3000 (admin/admin)
```

Local venv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export POSTGRES_DSN="postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
alembic upgrade head && make dev

pytest --cov=ledger --cov-report=term-missing
make bench           # Locust, 200 users, 2 min, POST /api/transfers
```

`POST /api/transfers/` posts a balanced journal keyed by `idempotency_key`; balances, journals and reconciliation reports are under `/api/accounts/`, `/api/journals/` and `/api/reports/`. Prometheus counters and histograms (`ledger_tx_total`, `ledger_posting_latency_ms`, `ledger_serialization_retries_total`, `ledger_recon_mismatches_total`) are on `/metrics`, with OTEL spans on every business path.

## Limitations

- **No committed coverage or benchmark artifact.** `make bench` and `pytest --cov` are runnable but their outputs are not in the repo, so no performance or coverage number is asserted.
- **Single-currency journals.** FX settlement is modelled as two journals through a clearing account; native multi-currency posting is not shipped.
- **Single-tenant.** There is no `entity_id` partitioning and no authentication on the admin surface.
- **SERIALIZABLE has a cost.** The `40001` retry path is exercised by tests but has not been profiled under contention at scale.
- **Reconciliation matching is deliberately simple** — exact `ref` first, then amount within ±0.01. No fuzzy payee matching.

## License

MIT — see [LICENSE](LICENSE).
