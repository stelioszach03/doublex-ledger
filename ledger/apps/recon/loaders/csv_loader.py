from __future__ import annotations

import csv
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List


def load_csv(path: str | Path, currency: str = "USD") -> List[Dict[str, str]]:
    p = Path(path)
    rows: List[Dict[str, str]] = []
    with p.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "external_id": row.get("external_id") or row.get("id") or "",
                    "amount": Decimal(row["amount"]),
                    "currency": (row.get("currency") or currency).upper(),
                }
            )
    return rows

