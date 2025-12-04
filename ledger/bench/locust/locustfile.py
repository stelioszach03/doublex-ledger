from __future__ import annotations

import os
import random
import uuid
from collections import deque
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock

from locust import HttpUser, events, task


ACCOUNT_POOL: list[str] = []
POOL_LOCK = Lock()
LAST_KEYS: deque[str] = deque(maxlen=1000)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _ensure_account_pool(user: "LedgerUser") -> None:
    desired = _env_int("ACCOUNT_POOL_SIZE", 100)
    ccy = os.getenv("ACCOUNT_CCY", "EUR").upper()
    with POOL_LOCK:
        if len(ACCOUNT_POOL) >= desired:
            return
    # Fetch existing
    r = user.client.get("/api/accounts", name="GET /api/accounts")
    if r.ok:
        try:
            for acc in r.json():
                if acc.get("ccy", "").upper() == ccy:
                    ACCOUNT_POOL.append(acc["code"])
        except Exception:
            pass
    # Create missing
    to_create = max(0, desired - len(ACCOUNT_POOL))
    for i in range(to_create):
        code = f"LT-{ccy}-{i:05d}"
        payload = {"code": code, "name": code, "ccy": ccy}
        resp = user.client.post("/api/accounts", json=payload, name="POST /api/accounts")
        if resp.status_code in (200, 201, 409):
            ACCOUNT_POOL.append(code)


def _random_amount() -> Decimal:
    amt = Decimal(random.uniform(1, 1000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return amt


class LedgerUser(HttpUser):
    abstract = False

    def on_start(self):
        _ensure_account_pool(self)

    def wait_time(self):
        # Approximate target global RPS by distributing across active users
        txps = float(os.getenv("TX_PER_SEC", "0"))
        if txps <= 0:
            return 0.0
        users = 1
        try:
            if self.environment and self.environment.runner:
                users = max(1, int(self.environment.runner.user_count))
        except Exception:
            users = 1
        per_user_rps = max(txps / users, 0.01)
        return 1.0 / per_user_rps

    def _next_idempotency_key(self) -> str:
        # Occasionally reuse a previous key to exercise dedupe
        reuse_rate = float(os.getenv("DEDUPE_RATE", "0.05"))
        if LAST_KEYS and random.random() < reuse_rate:
            return random.choice(list(LAST_KEYS))
        k = str(uuid.uuid4())
        LAST_KEYS.append(k)
        return k

    def _pick_two_accounts(self) -> tuple[str, str]:
        if len(ACCOUNT_POOL) < 2:
            _ensure_account_pool(self)
        a, b = random.sample(ACCOUNT_POOL, 2)
        return a, b

    @task
    def post_transfer(self):
        a1, a2 = self._pick_two_accounts()
        ccy = os.getenv("ACCOUNT_CCY", "EUR").upper()
        amt = _random_amount()
        key = self._next_idempotency_key()
        payload = {
            "client_id": "locust",
            "idempotency_key": key,
            "value_date": date.today().isoformat(),
            "memo": "load test",
            "entries": [
                {"account_code": a1, "amount": str(amt), "ccy": ccy, "memo": "bench"},
                {"account_code": a2, "amount": str(-amt), "ccy": ccy, "memo": "bench"},
            ],
        }
        with self.client.post(
            "/api/transfers",
            json=payload,
            name="POST /api/transfers",
            catch_response=True,
        ) as resp:
            if not resp.ok:
                resp.failure(f"HTTP {resp.status_code}")
                return
            try:
                data = resp.json()
                if data.get("status") not in {"applied", "duplicate"}:
                    resp.failure(f"Unexpected status {data.get('status')}")
                else:
                    resp.success()
            except Exception as e:
                resp.failure(str(e))


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    stats = environment.stats.get("POST /api/transfers")
    if not stats:
        print("No stats for POST /api/transfers")
        return
    total = stats.num_requests
    fails = stats.num_failures
    succ_rate = 0.0 if total == 0 else (1 - fails / total) * 100.0
    # p95 latency (ms)
    try:
        p95 = stats.get_current_response_time_percentile(0.95)
    except Exception:
        try:
            p95 = stats.get_response_time_percentile(0.95)
        except Exception:
            p95 = 0
    # Achieved TPS (requests / duration)
    duration = max(1e-9, environment.stats.total.last_request_timestamp - environment.stats.total.start_time)
    tps = total / duration
    print(
        {
            "requests": total,
            "failed": fails,
            "success_rate_pct": round(succ_rate, 2),
            "p95_ms": round(p95, 2),
            "achieved_tps": round(tps, 2),
        }
    )
