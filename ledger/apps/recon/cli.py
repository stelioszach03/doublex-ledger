from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from rich import print
from rich.console import Console
from rich.table import Table

from ledger.apps.recon.service import (
    import_csv_and_match,
    save_html_report,
    load_full_summary_from_disk,
)

app = typer.Typer(help="Reconciliation CLI")


@app.command("import-csv")
def import_csv(
    date_: str = typer.Option(..., "--date", help="Reconciliation date YYYY-MM-DD"),
    path: Path = typer.Option(..., "--path", exists=True, readable=True, help="Path to external CSV"),
    mapping: str = typer.Option("generic", "--mapping", help="CSV mapping key (e.g. bank_xyz)"),
    default_ccy: str = typer.Option(None, "--ccy", help="Default currency if missing in file"),
):
    d = date.fromisoformat(date_)
    summary = import_csv_and_match(recon_date=d, path=str(path), mapping=mapping, default_ccy=default_ccy)
    html_path = save_html_report(summary)

    # Pretty console table
    tbl = Table(title=f"Reconciliation {d.isoformat()}")
    tbl.add_column("Metric")
    tbl.add_column("Value")
    tbl.add_row("Matched", str(summary.matched_count))
    tbl.add_row("Unmatched External", str(summary.unmatched_external_count))
    tbl.add_row("Unmatched Ledger", str(summary.unmatched_ledger_count))
    console = Console()
    console.print(tbl)
    print({"html": str(html_path)})


@app.command("report")
def report(
    date_: str = typer.Option(..., "--date", help="Reconciliation date YYYY-MM-DD"),
):
    d = date.fromisoformat(date_)
    summary = load_full_summary_from_disk(d)
    if summary is None:
        print({"error": f"No report found for {d.isoformat()}"})
        raise typer.Exit(code=1)

    html_path = save_html_report(summary)
    # Pretty table
    tbl = Table(title=f"Reconciliation {d.isoformat()}")
    tbl.add_column("Metric")
    tbl.add_column("Value")
    tbl.add_row("Matched", str(summary.matched_count))
    tbl.add_row("Unmatched External", str(summary.unmatched_external_count))
    tbl.add_row("Unmatched Ledger", str(summary.unmatched_ledger_count))
    console = Console()
    console.print(tbl)
    print({"html": str(html_path)})


def main() -> None:
    app()


if __name__ == "__main__":
    main()
