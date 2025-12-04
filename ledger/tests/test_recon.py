from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ledger.apps.recon.loaders.csv_loader import load_csv
from ledger.apps.recon.reconcile import ReconRow, reconcile_rows


def test_csv_loader(tmp_path: Path):
    csv_file = tmp_path / "ext.csv"
    csv_file.write_text("external_id,amount\na,10\nb,5\n")
    rows = load_csv(csv_file, currency="USD")
    assert len(rows) == 2
    assert rows[0]["external_id"] == "a"
    assert rows[0]["amount"] == Decimal("10")


def test_simple_recon_match():
    ledger = [ReconRow("a", Decimal("10.00"), "USD")]
    ext = [ReconRow("a", Decimal("10.00"), "USD")]
    res = reconcile_rows(ledger, ext)
    assert len(res.matched) == 1
    assert not res.unmatched_external
    assert not res.unmatched_ledger
