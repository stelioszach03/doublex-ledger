from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Tuple

from ledger.apps.recon.loaders.csv_loader import load_csv
from ledger.apps.api.observability.metrics import LEDGER_RECON_MISMATCHES_TOTAL


@dataclass
class ReconRow:
    external_id: str
    amount: Decimal
    currency: str


@dataclass
class ReconResult:
    matched: List[Tuple[ReconRow, ReconRow]]
    unmatched_external: List[ReconRow]
    unmatched_ledger: List[ReconRow]


def reconcile_rows(
    ledger_rows: Iterable[ReconRow], external_rows: Iterable[ReconRow]
) -> ReconResult:
    by_id_ledger = {r.external_id: r for r in ledger_rows if r.external_id}
    by_id_external = {r.external_id: r for r in external_rows if r.external_id}

    matched: List[Tuple[ReconRow, ReconRow]] = []
    unmatched_external: List[ReconRow] = []
    unmatched_ledger: List[ReconRow] = []

    # Match by external_id exact first
    for ext_id, ext_row in by_id_external.items():
        led_row = by_id_ledger.get(ext_id)
        if led_row and led_row.amount == ext_row.amount and led_row.currency == ext_row.currency:
            matched.append((led_row, ext_row))
        else:
            unmatched_external.append(ext_row)

    # Remaining ledger unmatched
    matched_ids = {a.external_id for a, _ in matched}
    for led_id, led_row in by_id_ledger.items():
        if led_id not in matched_ids:
            unmatched_ledger.append(led_row)

    result = ReconResult(matched=matched, unmatched_external=unmatched_external, unmatched_ledger=unmatched_ledger)
    # Observe mismatches (sum of both sides)
    try:
        LEDGER_RECON_MISMATCHES_TOTAL.inc(len(result.unmatched_external) + len(result.unmatched_ledger))
    except Exception:
        # Metrics should not break business logic
        pass
    return result


def reconcile_csv(csv_path: str, currency: str = "USD") -> ReconResult:
    external = [ReconRow(**row) for row in load_csv(csv_path, currency=currency)]
    # For MVP we use the external twice to simulate roundtrip (replace with DB lookup)
    ledger_rows = list(external)
    return reconcile_rows(ledger_rows, external)
