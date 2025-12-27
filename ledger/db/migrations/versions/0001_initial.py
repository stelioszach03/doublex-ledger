"""
Initial schema: core ledger tables, enums, constraints, triggers.

This migration creates:
- Enums: account_status, transfer_status, recon_status
- Tables: accounts, journals, entries, transfer_requests,
          balance_snapshots, fx_rates, eod_closes, recon_exceptions
- Indexes and unique constraints
- DB-level enforcement:
  - Non-zero entry amount
  - Journal balance trigger (sum(amount) = 0 with tolerance 1e-6)
  - Entry currency matches account currency
  - Database default isolation SERIALIZABLE (best-effort)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums ---
    # Pre-create named enums with create_type disabled on column usage to avoid duplicate CREATE TYPE
    account_status = sa.Enum(
        "active",
        "blocked",
        name="account_status",
        create_type=False,
    )
    transfer_status = sa.Enum(
        "applied",
        "duplicate",
        "rejected",
        name="transfer_status",
        create_type=False,
    )
    recon_status = sa.Enum(
        "open",
        "resolved",
        name="recon_status",
        create_type=False,
    )

    bind = op.get_bind()
    account_status.create(bind, checkfirst=True)
    transfer_status.create(bind, checkfirst=True)
    recon_status.create(bind, checkfirst=True)

    # --- Tables ---
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("ccy", sa.String(), nullable=False),
        sa.Column("status", account_status, nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.UniqueConstraint("code", name="uq_accounts_code"),
    )
    # Note: unique constraint already implies an index; the models also requested an index
    op.create_index("ix_accounts_code", "accounts", ["code"], unique=True)

    op.create_table(
        "journals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_table(
        "entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "journal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("journals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("ccy", sa.String(), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.CheckConstraint("amount <> 0", name="ck_entries_amount_nonzero"),
    )
    op.create_index("ix_entries_journal_id", "entries", ["journal_id"], unique=False)
    op.create_index("ix_entries_account_id", "entries", ["account_id"], unique=False)
    op.create_index("ix_entries_account_ccy", "entries", ["account_id", "ccy"], unique=False)

    op.create_table(
        "transfer_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("status", transfer_status, nullable=False),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_transfer_req_client_idem"),
    )

    op.create_table(
        "balance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance", sa.Numeric(20, 6), nullable=True),
        sa.Column("ccy", sa.String(), nullable=False),
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("pair", sa.String(), nullable=False),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.UniqueConstraint("pair", "valid_from", name="uq_fx_pair_valid_from"),
    )

    op.create_table(
        "eod_closes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("close_date", sa.Date(), nullable=False, unique=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "recon_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("recon_date", sa.Date(), nullable=False),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("ccy", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", recon_status, nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    # --- DB-level helpers: triggers and isolation ---
    op.execute(
        sa.text(
            """
-- Ensure journals are balanced: sum(amount) = 0 per journal_id with tolerance
-- Enforce Account.ccy = Entry.ccy
-- Set default isolation level to SERIALIZABLE

-- Amount non-zero (idempotent)
ALTER TABLE IF EXISTS entries
    ADD CONSTRAINT IF NOT EXISTS ck_entries_amount_nonzero CHECK (amount <> 0);

-- Tolerance 1e-6 for balance
CREATE OR REPLACE FUNCTION check_journal_balanced() RETURNS TRIGGER AS $$
DECLARE
    v_total NUMERIC;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO v_total
    FROM entries
    WHERE journal_id = COALESCE(NEW.journal_id, OLD.journal_id);

    IF abs(v_total) > 0.000001 THEN
        RAISE EXCEPTION 'Journal % is not balanced (total=%)', COALESCE(NEW.journal_id, OLD.journal_id), v_total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL; -- AFTER trigger
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_journal_balanced ON entries;
CREATE CONSTRAINT TRIGGER trg_check_journal_balanced
AFTER INSERT OR UPDATE OR DELETE ON entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_journal_balanced();

-- Enforce account currency on entries
CREATE OR REPLACE FUNCTION check_entry_ccy() RETURNS TRIGGER AS $$
DECLARE
    v_acc_ccy TEXT;
BEGIN
    SELECT ccy INTO v_acc_ccy FROM accounts WHERE id = NEW.account_id;
    IF v_acc_ccy IS NULL THEN
        RAISE EXCEPTION 'Account % not found', NEW.account_id;
    END IF;
    IF v_acc_ccy <> NEW.ccy THEN
        RAISE EXCEPTION 'Currency mismatch for account %: account=% entry=%', NEW.account_id, v_acc_ccy, NEW.ccy
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_entry_ccy ON entries;
CREATE TRIGGER trg_check_entry_ccy
BEFORE INSERT OR UPDATE ON entries
FOR EACH ROW EXECUTE FUNCTION check_entry_ccy();

-- Set default DB isolation (requires superuser)
DO $$ BEGIN
  EXECUTE 'ALTER DATABASE ' || current_database() || ' SET default_transaction_isolation = ''serializable''';
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'Skipping ALTER DATABASE, insufficient privileges';
END $$;
"""
        )
    )


def downgrade() -> None:
    # Drop triggers and functions first (idempotent drops)
    op.execute(
        sa.text(
            """
DROP TRIGGER IF EXISTS trg_check_journal_balanced ON entries;
DROP TRIGGER IF EXISTS trg_check_entry_ccy ON entries;
DROP FUNCTION IF EXISTS check_journal_balanced() CASCADE;
DROP FUNCTION IF EXISTS check_entry_ccy() CASCADE;
"""
        )
    )

    # Drop tables in dependency-safe order
    op.drop_table("recon_exceptions")
    op.drop_table("eod_closes")
    op.drop_table("fx_rates")
    op.drop_table("balance_snapshots")
    op.drop_index("ix_entries_account_ccy", table_name="entries")
    op.drop_index("ix_entries_account_id", table_name="entries")
    op.drop_index("ix_entries_journal_id", table_name="entries")
    op.drop_table("entries")
    op.drop_table("journals")
    op.drop_index("ix_accounts_code", table_name="accounts")
    op.drop_table("accounts")
    op.drop_table("transfer_requests")

    # Drop enums
    bind = op.get_bind()
    sa.Enum(name="recon_status").drop(bind, checkfirst=True)
    sa.Enum(name="transfer_status").drop(bind, checkfirst=True)
    sa.Enum(name="account_status").drop(bind, checkfirst=True)
