from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable


@dataclass
class BalanceRow:
    account_code: str
    currency: str
    balance: Decimal


def summarize_balances(rows: Iterable[BalanceRow]) -> Dict[str, Decimal]:
    summary: Dict[str, Decimal] = {}
    for r in rows:
        key = f"{r.account_code}:{r.currency}"
        summary[key] = summary.get(key, Decimal(0)) + r.balance
    return summary

