![CI](https://github.com/stelioszach03/doublex-ledger/actions/workflows/ci.yml/badge.svg)
<!-- CI trigger: README touch to re-run workflow -->

# DoubleX Ledger

Production‑ready double‑entry ledger with strong consistency (SERIALIZABLE), idempotency, EoD, FX, recon, and first‑class observability.

Highlights
- All tests passing; coverage 94% (pytest + hypothesis) across domain, API, recon, observability.
- ACID semantics: SERIALIZABLE isolation, DB‑level balance + currency enforcement.
- Idempotent postings with retries; safe under concurrency (advisory locks pattern).
- End‑of‑Day close: balance snapshots, backdating guard by latest closed date.
- FX settlement (two‑journal method + clearing), upsertable rates, precise rounding.
- Recon CLI: import CSV, match by ref/amount(±tol), write CSV/JSON/HTML, and serve via API.
- Observability: Prometheus metrics, OTEL tracing; Grafana “Ledger SLO” dashboard.
- CI (GitHub Actions): Postgres service, Alembic, pytest + coverage, Docker builds.

What’s inside
- Domain: double_entry, eod, fx, idempotency, locking.
- API: FastAPI (/api/...), Prometheus /metrics, OTEL auto‑instrumentation.
- Data: SQLAlchemy 2.0 models, Alembic migrations, Postgres triggers.
- Tooling: Locust bench, Typer recon CLI, Grafana provisioning, Docker/Compose.

Quickstart
- Local (venv)
  - python -m venv .venv && . .venv/bin/activate
  - pip install -r requirements.txt
  - export POSTGRES_DSN="postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
  - make dev
- Docker Compose
  - cp .env.example .env (ή χρησιμοποιήστε το έτοιμο .env)
  - make compose-up
  - API: http://localhost:8000 • Prometheus: http://localhost:9090 • Grafana: http://localhost:3000

API Examples (under /api)
- Health + version: `curl -s localhost:8000/api/health`
- Create account:
  `curl -X POST localhost:8000/api/accounts -H 'content-type: application/json' -d '{"code":"1001-CASH","name":"Cash","ccy":"EUR"}'`
- Balance: `curl -s localhost:8000/api/accounts/1001-CASH/balance`
- Post transfer (idempotent): see transfers payload in tests/README samples.
- Journals: `curl -s "localhost:8000/api/journals?from=2025-09-01&to=2025-09-30&account=1001-CASH"`
- Recon summary: `curl -s "localhost:8000/api/reports/recon?date=2025-09-01"`

Engineering details
- Tests: property tests for balanced postings; rich API/unit coverage including EoD, FX, idempotency, recon.
- EoD: cut‑off window; idempotent close; balance snapshots at boundary ts; backdating guard.
- FX: store/upsert rates; normalize/invert pairs; settlement in two legs via clearing.
- Recon: exact ref match then amount±0.01; write artifacts (CSV/JSON/HTML); insert ReconException.
- Observability: metrics (ledger_tx_total, ledger_posting_latency_ms, ledger_serialization_retries_total, ledger_recon_mismatches_total), spans (post_journal, get_balance).

Bench & KPIs
- Locust for POST /api/transfers. Env: TX_PER_SEC, ACCOUNT_POOL_SIZE, ACCOUNT_CCY, DEDUPE_RATE.
- Reports success rate, p95 latency, failures, achieved TPS.

Runbook
- Migrations: MIGRATE_ON_START=true (Compose) ή `make migrate` / `make makemigration m="..."`.
- Logs: LOG_FORMAT=json για structured logging (Loguru).
- OTEL: set OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://otel-collector:4318).

Roadmap
- Native multi‑currency journals; multi‑entity partitioning.
- AuthN/Z for API; SCD for Accounts metadata; richer dashboards.
