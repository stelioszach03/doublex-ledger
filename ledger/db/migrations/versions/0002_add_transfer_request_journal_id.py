"""
Add journal_id to transfer_requests for idempotent success linkage.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0002_add_tr_journal"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transfer_requests",
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("journals.id"), nullable=True),
    )
    op.create_index("ix_transfer_requests_journal_id", "transfer_requests", ["journal_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transfer_requests_journal_id", table_name="transfer_requests")
    op.drop_column("transfer_requests", "journal_id")
