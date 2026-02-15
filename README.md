![CI](https://github.com/stelioszach03/doublex-ledger/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-informational)

# DoubleX Ledger

A production-grade **double-entry accounting ledger**: SERIALIZABLE isolation,
idempotent postings, end-of-day close, FX settlement, reconciliation CLI, and
first-class observability. FastAPI on top, PostgreSQL doing the work.

> Every guarantee is enforced where it's hardest to break — at the database
> layer. The service code is thin on top: it reads, validates, posts under a
> transaction, and surfaces the solver's view. The invariants don't rely on
> application discipline.

---

## Live demo

| What | URL |
| --- | --- |
| Editorial landing (with live idempotency widget) | <https://stelioszach.com/doublex-ledger/> |
| OpenAPI / Swagger docs | <https://stelioszach.com/doublex-ledger/live/docs> |
| Prometheus metrics | <https://stelioszach.com/doublex-ledger/live/metrics> |
| Health | <https://stelioszach.com/doublex-ledger/health> |

Try the idempotency contract for yourself:

```bash
BASE=https://stelioszach.com/doublex-ledger/live/api
IK="my-key-$(date +%s)"

# first call → status=applied, new journal_id
curl -s -X POST $BASE/transfers/ \
  -H 'content-type: application/json' \
  -d "{\"client_id\":\"demo\",\"idempotency_key\":\"$IK\",\"memo\":\"test\",
        \"entries\":[
          {\"account_code\":\"DEMO-CASH\",\"amount\":\"10.00\",\"ccy\":\"EUR\"},
          {\"account_code\":\"DEMO-REV\", \"amount\":\"-10.00\",\"ccy\":\"EUR\"}
       ]}"

# replay with the SAME idempotency_key → status=duplicate, same journal_id, no movement
curl -s -X POST $BASE/transfers/ -H 'content-type: application/json' -d "$PAYLOAD"
```

---

## Highlights

- **SERIALIZABLE isolation** on Postgres — no lost updates, no phantom rows.
- **Balanced-by-construction**: a database trigger refuses any journal whose
  entries don't sum to zero per currency. Application code can't skip it.
- **Idempotent postings** keyed by `(client_id, idempotency_key)`. Retries
  return `status=duplicate` with the original `journal_id`; balances never
  double-move.
- **Correct advisory locking** — the `pg_advisory_lock` key is mapped into
  signed int64 so there's no `bigint out of range` crash on hash collisions
  (see `ledger/domain/locking.py`).
- **End-of-Day close**: idempotent, cut-off-aware, guards against back-dated
  postings before the last closed day; emits balance snapshots.
- **FX settlement** in two journals via a clearing account; upsertable rates,
  precise rounding, currency-normalised pairs.
- **Reconciliation CLI**: bulk-import bank CSVs, match by `ref` first then by
  `amount ±0.01`, write CSV/JSON/HTML artifacts, and serve the results over
  `/api/reports/recon`.
- **Observability**: Prometheus counters + histograms
  (`ledger_tx_total`, `ledger_posting_latency_ms`,
  `ledger_serialization_retries_total`, `ledger_recon_mismatches_total`),
  OTEL spans on every business path, and a pre-provisioned Grafana
  "Ledger SLO" dashboard.
- **CI** across Python 3.11 / 3.12 with a live Postgres service, full
  Alembic upgrade, pytest + coverage, and Docker image builds.

---

## Architecture

```
  client  ── POST /api/transfers  ▶  FastAPI (Pydantic v2)
                                        │
                                        ▼
                  validate → resolve plan → acquire advisory lock
                                        │
                                        ▼
                           BEGIN SERIALIZABLE TRANSACTION
                                        │
                           INSERT journal · INSERT postings
                                        │           │
                     check_balanced trigger   idempotency_journal
                                        ▼
                              COMMIT (or retry on 40001)
                                        │
                                        ▼
                           response + Prometheus + OTEL span
```

- **Domain** (`ledger/domain/`): `double_entry`, `eod`, `fx`, `idempotency`,
  `locking`.
- **API** (`ledger/apps/api/`): FastAPI routers at `/api/...`, Prometheus at
  `/metrics`, OTEL auto-instrumentation.
- **Data** (`ledger/db/`): SQLAlchemy 2.0 models, Alembic migrations,
  Postgres triggers for invariants.
- **Tooling**: Typer recon CLI, Locust bench, Grafana provisioning,
  docker-compose.

---

## Quickstart

### Docker Compose (recommended)

```bash
cp .env.example .env
make compose-up
# API         → http://localhost:8000
# Prometheus  → http://localhost:9090
# Grafana     → http://localhost:3000 (admin/admin)
```

### Local (venv)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export POSTGRES_DSN="postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
alembic upgrade head
make dev           # uvicorn on :8000, hot reload
```

### Seed demo accounts

```bash
curl -X POST http://localhost:8000/api/accounts/ \
  -H 'content-type: application/json' \
  -d '{"code":"DEMO-CASH","name":"Demo Cash","ccy":"EUR"}'
curl -X POST http://localhost:8000/api/accounts/ \
  -H 'content-type: application/json' \
  -d '{"code":"DEMO-REV","name":"Demo Revenue","ccy":"EUR"}'
```

---

## API reference

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/accounts/` | Create an account |
| `GET`  | `/api/accounts/{code}/balance` | Current balance (as-of snapshot) |
| `POST` | `/api/transfers/` | Post a balanced journal, idempotent by `idempotency_key` |
| `GET`  | `/api/journals/` | List / filter journals |
| `GET`  | `/api/journals/{id}` | Journal detail with entries |
| `GET`  | `/api/reports/recon` | Reconciliation report for a date |
| `GET`  | `/api/reports/balances` | Full balance sheet at a cut-off |
| `GET`  | `/api/health`, `/ready`, `/metrics` | Liveness / readiness / metrics |

Full machine-readable spec: <https://stelioszach.com/doublex-ledger/live/openapi.json>.

---

## Guarantees

| Property | How it's enforced |
| --- | --- |
| Entries sum to zero per currency | Postgres trigger on `postings` |
| No posting into closed day | `eod.check_closed_before(value_date)` |
| Idempotent writes under retry | Unique index on `(client_id, idempotency_key)` |
| No ghost accounts | Foreign key + `status` check |
| Serializable reads & writes | `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` |
| Deterministic advisory locks | Signed-int64 mapping of SHA-256 prefix |

---

## Testing

```bash
# unit + integration + API tests
pytest --cov=ledger --cov-report=term-missing

# property tests (Hypothesis) for balanced-posting invariants
pytest tests/test_double_entry_properties.py
```

CI runs on every push on Python 3.11 and 3.12 with a real Postgres service and
uploads coverage artefacts.

---

## Observability

- **Metrics** — `GET /metrics` exposes Prometheus counters and histograms for
  transaction counts, posting latency, serialization retries, and recon
  mismatches.
- **Traces** — FastAPI + SQLAlchemy are instrumented via OpenTelemetry; export
  OTLP to any collector by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.
- **Dashboards** — `grafana/provisioning/` boots a "Ledger SLO" dashboard on
  first start of the Compose stack.
- **Logs** — `LOG_FORMAT=json` for structured Loguru output.

---

## Bench

```bash
make bench   # Locust, 200 users, 2 min, POST /api/transfers
```

Tunable via `TX_PER_SEC`, `ACCOUNT_POOL_SIZE`, `ACCOUNT_CCY`, `DEDUPE_RATE`.
Reports p95 latency, success rate, achieved TPS.

---

## Runbook

- **Migrations** — `make migrate` or set `MIGRATE_ON_START=true` in Compose.
- **Create a migration** — `make makemigration m="describe change"`.
- **Seed accounts** — `scripts/seed_demo.py`.
- **Open the Ledger SLO dashboard** — Grafana → "Ledger SLO".

---

## Roadmap

- Native multi-currency journals on a single posting.
- Multi-tenant partitioning (`entity_id` on every table).
- AuthN/Z for the admin surface.
- SCD-Type-2 history on `accounts` metadata.

---

## License

MIT — see [LICENSE](LICENSE).

---

Built in Athens by **Stelios Zacharioudakis** · <sdi2200243@di.uoa.gr> ·
[stelioszach.com](https://stelioszach.com)
