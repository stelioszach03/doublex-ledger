from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger.db import models
from ledger.db.session import SessionLocal


DECIMAL_TOL = Decimal("0.01")  # 1 cent default tolerance for reconciliation


@dataclass
class ExternalTx:
    external_ref: str
    amount: Decimal
    ccy: str


MAPPINGS: Dict[str, Dict[str, str]] = {
    # Example mapping for a hypothetical bank file
    # CSV headers -> normalized fields
    "bank_xyz": {
        "external_ref": "Ref",
        "amount": "Amount",
        "ccy": "Currency",
        # Optional field names we ignore here: Date, Description, etc.
    },
    "generic": {
        "external_ref": "external_ref",
        "amount": "amount",
        "ccy": "ccy",
    },
}


def _ensure_reports_dir() -> Path:
    out_dir = Path("ledger/apps/recon/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def load_external_csv(path: str | Path, mapping: str, default_ccy: Optional[str] = None) -> List[ExternalTx]:
    p = Path(path)
    mapdef = MAPPINGS.get(mapping)
    if not mapdef:
        raise ValueError(f"Unknown mapping: {mapping}")

    rows: List[ExternalTx] = []
    with p.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            try:
                extref = (raw.get(mapdef["external_ref"], "") or "").strip()
                amt = Decimal(str(raw.get(mapdef["amount"])) if raw.get(mapdef["amount"]) is not None else "0")
                ccy = (raw.get(mapdef["ccy"]) or default_ccy or "").upper()
                rows.append(ExternalTx(external_ref=extref, amount=amt, ccy=ccy))
            except Exception as e:
                logger.warning("Skipping row due to parse error: {}", e)
                continue
    return rows


@dataclass
class ReconMatch:
    journal_id: str
    created_at: str
    amount: str
    ccy: str
    external_ref: str


@dataclass
class ReconSummary:
    recon_date: date
    matched_count: int
    unmatched_external_count: int
    unmatched_ledger_count: int
    matched: List[ReconMatch]
    unmatched_external: List[ExternalTx]
    unmatched_ledger: List[Tuple[str, Decimal, str]]  # (journal_id, amount, ccy)


def _journal_candidates_for_date(db: Session, d: date) -> Dict[Tuple[str, Decimal, str], models.Journal]:
    """
    Build candidate amounts keyed by (journal_id, abs(entry.amount), entry.ccy) for journals on date `d`.
    Returns mapping from (jid, amount, ccy) -> journal.
    """
    jrows: List[models.Journal] = (
        db.execute(select(models.Journal).where(models.Journal.value_date == d)).scalars().all()
    )
    mapping: Dict[Tuple[str, Decimal, str], models.Journal] = {}
    for j in jrows:
        # Eager load entries for the journal
        _ = [e.id for e in j.entries]
        for e in j.entries:
            key = (str(j.id), abs(Decimal(e.amount)), e.ccy.upper())
            mapping[key] = j
    return mapping


def _closest_amount_match(
    amount: Decimal, ccy: str, candidates: Dict[Tuple[str, Decimal, str], models.Journal], used: set
) -> Optional[Tuple[str, models.Journal, Decimal]]:
    best: Optional[Tuple[str, models.Journal, Decimal]] = None
    for (jid, amt, c), j in candidates.items():
        if c != ccy.upper() or jid in used:
            continue
        diff = abs(amt - abs(amount))
        if diff <= DECIMAL_TOL:
            if best is None or diff < best[2]:
                best = (jid, j, diff)
    return best


def import_csv_and_match(
    *,
    recon_date: date,
    path: str | Path,
    mapping: str,
    default_ccy: Optional[str] = None,
    db: Optional[Session] = None,
) -> ReconSummary:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        externals = load_external_csv(path, mapping, default_ccy)
        candidates = _journal_candidates_for_date(db, recon_date)

        matched: List[ReconMatch] = []
        unmatched_external: List[ExternalTx] = []
        used_journals: set[str] = set()

        # Match by exact external_ref first
        ext_map = {e.external_ref: e for e in externals if e.external_ref}
        if ext_map:
            for key, e in list(ext_map.items()):
                # Find journal with same external_ref on recon_date
                j = (
                    db.execute(
                        select(models.Journal)
                        .where(models.Journal.value_date == recon_date, models.Journal.external_ref == key)
                        .order_by(models.Journal.created_at.desc())
                    )
                    .scalars()
                    .first()
                )
                if j is not None and str(j.id) not in used_journals:
                    used_journals.add(str(j.id))
                    matched.append(
                        ReconMatch(
                            journal_id=str(j.id),
                            created_at=j.created_at.isoformat() if j.created_at else "",
                            amount=str(e.amount),
                            ccy=e.ccy,
                            external_ref=e.external_ref,
                        )
                    )
                    externals.remove(e)

        # Match remaining by amount within tolerance and currency
        for e in externals:
            best = _closest_amount_match(e.amount, e.ccy, candidates, used_journals)
            if best is None:
                unmatched_external.append(e)
            else:
                jid, j, _ = best
                used_journals.add(jid)
                matched.append(
                    ReconMatch(
                        journal_id=str(j.id),
                        created_at=j.created_at.isoformat() if j.created_at else "",
                        amount=str(e.amount),
                        ccy=e.ccy,
                        external_ref=e.external_ref,
                    )
                )

        # Unmatched ledger candidates (journals not used)
        unmatched_ledger: List[Tuple[str, Decimal, str]] = []
        for (jid, amt, ccy) in candidates.keys():
            if jid not in used_journals:
                unmatched_ledger.append((jid, amt, ccy))

        # Insert ReconException rows for unmatched externals
        # Ensure we're not in an implicit transaction from prior SELECTs
        try:
            db.commit()
        except Exception:
            pass
        for e in unmatched_external:
            db.add(
                models.ReconException(
                    recon_date=recon_date,
                    external_ref=e.external_ref,
                    amount=e.amount,
                    ccy=e.ccy,
                    reason="Unmatched external",
                )
            )
        db.commit()

        summary = ReconSummary(
            recon_date=recon_date,
            matched_count=len(matched),
            unmatched_external_count=len(unmatched_external),
            unmatched_ledger_count=len(unmatched_ledger),
            matched=matched,
            unmatched_external=unmatched_external,
            unmatched_ledger=unmatched_ledger,
        )

        # Write artifacts
        out_dir = _ensure_reports_dir()
        stem = f"recon-{recon_date.isoformat()}"
        # JSON summary
        (out_dir / f"{stem}.json").write_text(
            json.dumps(
                {
                    "date": recon_date.isoformat(),
                    "matched_count": summary.matched_count,
                    "unmatched_external_count": summary.unmatched_external_count,
                    "unmatched_ledger_count": summary.unmatched_ledger_count,
                },
                indent=2,
            )
        )
        # CSVs
        with (out_dir / f"{stem}-matched.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["journal_id", "created_at", "amount", "ccy", "external_ref"])
            for m in matched:
                w.writerow([m.journal_id, m.created_at, m.amount, m.ccy, m.external_ref])
        with (out_dir / f"{stem}-unmatched-external.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["external_ref", "amount", "ccy"])
            for e in unmatched_external:
                w.writerow([e.external_ref, str(e.amount), e.ccy])
        with (out_dir / f"{stem}-unmatched-ledger.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["journal_id", "amount", "ccy"])
            for jid, amt, c in unmatched_ledger:
                w.writerow([jid, str(amt), c])

        return summary
    finally:
        if close_db:
            db.close()


def render_html_report(summary: ReconSummary) -> str:
    from rich.table import Table
    from rich.console import Console
    from rich.theme import Theme
    from rich.markdown import Markdown
    from io import StringIO

    md = Markdown(f"""
# Reconciliation Report — {summary.recon_date.isoformat()}

Matched: {summary.matched_count}  
Unmatched (external): {summary.unmatched_external_count}  
Unmatched (ledger): {summary.unmatched_ledger_count}
""")

    tbl_m = Table(title="Matched")
    for col in ["journal_id", "created_at", "amount", "ccy", "external_ref"]:
        tbl_m.add_column(col)
    for m in summary.matched:
        tbl_m.add_row(m.journal_id, m.created_at, m.amount, m.ccy, m.external_ref)

    tbl_ue = Table(title="Unmatched External")
    for col in ["external_ref", "amount", "ccy"]:
        tbl_ue.add_column(col)
    for e in summary.unmatched_external:
        tbl_ue.add_row(e.external_ref, str(e.amount), e.ccy)

    tbl_ul = Table(title="Unmatched Ledger")
    for col in ["journal_id", "amount", "ccy"]:
        tbl_ul.add_column(col)
    for jid, amt, c in summary.unmatched_ledger:
        tbl_ul.add_row(jid, str(amt), c)

    console = Console(record=True, width=120, theme=Theme({}))
    console.print(md)
    console.print(tbl_m)
    console.print(tbl_ue)
    console.print(tbl_ul)
    html = console.export_html(inline_styles=True)
    return html


def save_html_report(summary: ReconSummary) -> Path:
    out_dir = _ensure_reports_dir()
    html = render_html_report(summary)
    p = out_dir / f"recon-{summary.recon_date.isoformat()}.html"
    p.write_text(html)
    return p


def load_summary_from_disk(d: date) -> Optional[Dict[str, int | str]]:
    p = Path("ledger/apps/recon/reports") / f"recon-{d.isoformat()}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def load_full_summary_from_disk(d: date) -> Optional[ReconSummary]:
    out_dir = Path("ledger/apps/recon/reports")
    json_p = out_dir / f"recon-{d.isoformat()}.json"
    if not json_p.exists():
        return None
    try:
        data = json.loads(json_p.read_text())
    except Exception:
        return None

    matched_csv = out_dir / f"recon-{d.isoformat()}-matched.csv"
    unmatched_ext_csv = out_dir / f"recon-{d.isoformat()}-unmatched-external.csv"
    unmatched_led_csv = out_dir / f"recon-{d.isoformat()}-unmatched-ledger.csv"

    matched: List[ReconMatch] = []
    if matched_csv.exists():
        with matched_csv.open("r", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                matched.append(
                    ReconMatch(
                        journal_id=row.get("journal_id", ""),
                        created_at=row.get("created_at", ""),
                        amount=row.get("amount", "0"),
                        ccy=row.get("ccy", ""),
                        external_ref=row.get("external_ref", ""),
                    )
                )

    unmatched_external: List[ExternalTx] = []
    if unmatched_ext_csv.exists():
        with unmatched_ext_csv.open("r", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                amt = Decimal(str(row.get("amount", "0")))
                unmatched_external.append(
                    ExternalTx(external_ref=row.get("external_ref", ""), amount=amt, ccy=row.get("ccy", ""))
                )

    unmatched_ledger: List[Tuple[str, Decimal, str]] = []
    if unmatched_led_csv.exists():
        with unmatched_led_csv.open("r", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                amt = Decimal(str(row.get("amount", "0")))
                unmatched_ledger.append((row.get("journal_id", ""), amt, row.get("ccy", "")))

    return ReconSummary(
        recon_date=d,
        matched_count=int(data.get("matched_count", 0)),
        unmatched_external_count=int(data.get("unmatched_external_count", 0)),
        unmatched_ledger_count=int(data.get("unmatched_ledger_count", 0)),
        matched=matched,
        unmatched_external=unmatched_external,
        unmatched_ledger=unmatched_ledger,
    )
