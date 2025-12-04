from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AccountStatus(str, Enum):
    active = "active"
    blocked = "blocked"


class TransferStatus(str, Enum):
    applied = "applied"
    duplicate = "duplicate"
    rejected = "rejected"


class ReconStatus(str, Enum):
    open = "open"
    resolved = "resolved"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    ccy: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(SAEnum(AccountStatus, name="account_status"), default=AccountStatus.active, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    entries: Mapped[list["Entry"]] = relationship("Entry", back_populates="account")


class Journal(Base):
    __tablename__ = "journals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    entries: Mapped[list["Entry"]] = relationship("Entry", back_populates="journal", cascade="all, delete-orphan")


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    journal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("journals.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)  # debit positive, credit negative
    ccy: Mapped[str] = mapped_column(String, nullable=False)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_entries_amount_nonzero"),
    )

    journal: Mapped[Journal] = relationship("Journal", back_populates="entries")
    account: Mapped[Account] = relationship("Account", back_populates="entries")


class TransferRequest(Base):
    __tablename__ = "transfer_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    status: Mapped[TransferStatus] = mapped_column(SAEnum(TransferStatus, name="transfer_status"), nullable=False)
    journal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("journals.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("client_id", "idempotency_key", name="uq_transfer_req_client_idem"),
    )


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ccy: Mapped[str] = mapped_column(String, nullable=False)


class FxRate(Base):
    __tablename__ = "fx_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    pair: Mapped[str] = mapped_column(String, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("pair", "valid_from", name="uq_fx_pair_valid_from"),
    )


class EodClose(Base):
    __tablename__ = "eod_closes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    close_date: Mapped[date] = mapped_column(Date, unique=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconException(Base):
    __tablename__ = "recon_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    recon_date: Mapped[date] = mapped_column(Date)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    ccy: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ReconStatus] = mapped_column(SAEnum(ReconStatus, name="recon_status"), default=ReconStatus.open, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


# Helpful indexes
Index("ix_entries_account_ccy", Entry.account_id, Entry.ccy)
