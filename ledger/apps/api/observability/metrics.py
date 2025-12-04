from __future__ import annotations

from prometheus_client import Counter, Histogram


REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    labelnames=("method", "path"),
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2.5,
        5,
        10,
    ),
)

# Domain metrics
LEDGER_TX_TOTAL = Counter(
    "ledger_tx_total",
    "Total ledger postings",
    labelnames=("result",),  # ok | fail
)

LEDGER_POSTING_LATENCY_MS = Histogram(
    "ledger_posting_latency_ms",
    "Latency of ledger postings in milliseconds",
    buckets=(
        1,
        2.5,
        5,
        10,
        25,
        50,
        100,
        250,
        500,
        1000,
        2500,
        5000,
    ),
)

LEDGER_SERIALIZATION_RETRIES_TOTAL = Counter(
    "ledger_serialization_retries_total",
    "Total number of serialization retry attempts",
)

LEDGER_RECON_MISMATCHES_TOTAL = Counter(
    "ledger_recon_mismatches_total",
    "Total reconciliation mismatches found",
)
