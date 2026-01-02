"""
LedgerX - Domain Models

Pydantic models and SQLAlchemy ORM models for the wallet and ledger domain.
These models enforce business invariants and provide type safety.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass


# =============================================================================
# Enums
# =============================================================================

class WalletStatus(str, Enum):
    """Wallet lifecycle states."""
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class WalletType(str, Enum):
    """Types of wallet accounts."""
    USER = "USER"
    MERCHANT = "MERCHANT"
    SYSTEM = "SYSTEM"
    FLOAT = "FLOAT"
    SETTLEMENT = "SETTLEMENT"


class TransactionStatus(str, Enum):
    """Transaction processing states."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    EXPIRED = "EXPIRED"


class TransactionType(str, Enum):
    """Types of financial transactions."""
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    TRANSFER = "TRANSFER"
    HOLD = "HOLD"
    RELEASE = "RELEASE"
    CAPTURE = "CAPTURE"
    REFUND = "REFUND"
    FEE = "FEE"
    ADJUSTMENT = "ADJUSTMENT"


class EntryType(str, Enum):
    """Ledger entry types (double-entry bookkeeping)."""
    DEBIT = "DEBIT"    # Money out (decreases asset balance)
    CREDIT = "CREDIT"  # Money in (increases asset balance)


class EntryStatus(str, Enum):
    """Ledger entry posting states."""
    PENDING = "PENDING"
    POSTED = "POSTED"
    VOIDED = "VOIDED"


class HoldStatus(str, Enum):
    """Hold lifecycle states."""
    ACTIVE = "ACTIVE"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class CurrencyCode(str, Enum):
    """Supported currencies (Asia-focused)."""
    USD = "USD"
    IDR = "IDR"  # Indonesian Rupiah
    PHP = "PHP"  # Philippine Peso
    VND = "VND"  # Vietnamese Dong
    THB = "THB"  # Thai Baht
    MYR = "MYR"  # Malaysian Ringgit
    SGD = "SGD"  # Singapore Dollar
    INR = "INR"  # Indian Rupee
    JPY = "JPY"  # Japanese Yen
    KRW = "KRW"  # Korean Won


# =============================================================================
# SQLAlchemy Base
# =============================================================================

class Base(DeclarativeBase):
    """SQLAlchemy declarative base with common configurations."""
    pass


# =============================================================================
# SQLAlchemy ORM Models
# =============================================================================

class WalletORM(Base):
    """
    Wallet entity - represents a financial account.
    
    Invariants:
    - external_id is unique across all wallets
    - Balance is never stored directly (computed from ledger)
    - Status changes are audited
    """
    __tablename__ = "wallets"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4
    )
    
    # Identity
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    wallet_type: Mapped[WalletType] = mapped_column(
        SQLEnum(WalletType),
        nullable=False,
        default=WalletType.USER
    )
    
    # Status
    status: Mapped[WalletStatus] = mapped_column(
        SQLEnum(WalletStatus),
        nullable=False,
        default=WalletStatus.ACTIVE
    )
    status_reason: Mapped[str | None] = mapped_column(String(255))
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Configuration
    currency: Mapped[CurrencyCode] = mapped_column(
        SQLEnum(CurrencyCode),
        nullable=False,
        default=CurrencyCode.USD
    )
    daily_limit: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("10000.0000")
    )
    monthly_limit: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("100000.0000")
    )
    
    # Metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict
    )
    
    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # Relationships
    balance: Mapped["WalletBalanceORM"] = relationship(
        "WalletBalanceORM",
        back_populates="wallet",
        uselist=False
    )
    
    __table_args__ = (
        Index("idx_wallets_status", "status", postgresql_where=status != WalletStatus.CLOSED),
    )


class WalletBalanceORM(Base):
    """
    Materialized wallet balance - cached for performance.
    
    Updated atomically via database triggers when ledger entries are posted.
    Verified via periodic reconciliation against ledger.
    """
    __tablename__ = "wallet_balances"
    
    wallet_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wallets.id"),
        primary_key=True
    )
    
    # Balance breakdown
    posted_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("0.0000"),
        nullable=False
    )
    pending_credits: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("0.0000"),
        nullable=False
    )
    pending_debits: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("0.0000"),
        nullable=False
    )
    held_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("0.0000"),
        nullable=False
    )
    
    # Note: available_balance is a generated column in PostgreSQL
    # available_balance = posted_balance - held_balance
    
    # Consistency tracking
    last_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    last_entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    
    # Audit
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # Relationships
    wallet: Mapped["WalletORM"] = relationship("WalletORM", back_populates="balance")
    
    @property
    def available_balance(self) -> Decimal:
        """Compute available balance (posted minus held)."""
        return self.posted_balance - self.held_balance


class TransactionORM(Base):
    """
    Transaction record - high-level record of a financial operation.
    
    Each transaction may create multiple ledger entries (double-entry).
    Idempotency is enforced via the idempotency_key.
    """
    __tablename__ = "transactions"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4
    )
    
    # Idempotency
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True
    )
    
    # Transaction details
    transaction_type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType),
        nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SQLEnum(TransactionStatus),
        nullable=False,
        default=TransactionStatus.PENDING
    )
    
    # Parties
    source_wallet_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wallets.id"),
        index=True
    )
    destination_wallet_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wallets.id"),
        index=True
    )
    
    # Amounts
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[CurrencyCode] = mapped_column(SQLEnum(CurrencyCode), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal("0.0000")
    )
    
    # References
    reference_id: Mapped[str | None] = mapped_column(String(128), index=True)
    parent_transaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id")
    )
    
    # Metadata
    description: Mapped[str | None] = mapped_column(String(512))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    
    # Processing
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(512))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # Relationships
    ledger_entries: Mapped[list["LedgerEntryORM"]] = relationship(
        "LedgerEntryORM",
        back_populates="transaction"
    )
    
    __table_args__ = (
        CheckConstraint(
            "source_wallet_id IS NULL OR destination_wallet_id IS NULL OR source_wallet_id != destination_wallet_id",
            name="chk_different_wallets"
        ),
        CheckConstraint(
            "source_wallet_id IS NOT NULL OR destination_wallet_id IS NOT NULL",
            name="chk_has_wallet"
        ),
        CheckConstraint("amount > 0", name="chk_positive_amount"),
        Index("idx_transactions_created", "created_at"),
    )


class LedgerEntryORM(Base):
    """
    Ledger entry - immutable journal entry.
    
    This is the source of truth for all balance calculations.
    Entries are append-only and cannot be modified after creation
    (only status can change from PENDING to POSTED or VOIDED).
    """
    __tablename__ = "ledger_entries"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    
    # Transaction reference
    transaction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id"),
        nullable=False,
        index=True
    )
    
    # Account affected
    wallet_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wallets.id"),
        nullable=False,
        index=True
    )
    
    # Entry details
    entry_type: Mapped[EntryType] = mapped_column(SQLEnum(EntryType), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[CurrencyCode] = mapped_column(SQLEnum(CurrencyCode), nullable=False)
    
    # Status
    status: Mapped[EntryStatus] = mapped_column(
        SQLEnum(EntryStatus),
        nullable=False,
        default=EntryStatus.PENDING
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Running balance (for statement generation)
    running_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    
    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    # Relationships
    transaction: Mapped["TransactionORM"] = relationship(
        "TransactionORM",
        back_populates="ledger_entries"
    )
    
    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_entry_positive_amount"),
        Index(
            "idx_ledger_wallet_posted",
            "wallet_id", "posted_at",
            postgresql_where=status == EntryStatus.POSTED
        ),
    )


class HoldORM(Base):
    """
    Hold record - funds reserved for pending transactions.
    
    Holds reduce available balance without affecting posted balance.
    They expire automatically after a configured duration.
    """
    __tablename__ = "holds"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4
    )
    
    # Reference
    wallet_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wallets.id"),
        nullable=False,
        index=True
    )
    transaction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id"),
        nullable=False,
        index=True
    )
    
    # Hold details
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[CurrencyCode] = mapped_column(SQLEnum(CurrencyCode), nullable=False)
    
    # Status
    status: Mapped[HoldStatus] = mapped_column(
        SQLEnum(HoldStatus),
        nullable=False,
        default=HoldStatus.ACTIVE
    )
    
    # Expiry
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_transaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id")
    )
    
    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_hold_positive_amount"),
        Index(
            "idx_holds_expires",
            "expires_at",
            postgresql_where=status == HoldStatus.ACTIVE
        ),
    )


class IdempotencyKeyORM(Base):
    """
    Idempotency key storage for safe request retries.
    
    Stores the hash of the original request and the response,
    allowing duplicate requests to return the same response.
    """
    __tablename__ = "idempotency_keys"
    
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    
    # Request details
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # Response caching
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    
    # Reference
    transaction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id")
    )
    
    # Lifecycle
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )
    
    __table_args__ = (
        Index("idx_idempotency_expires", "expires_at"),
    )


class AuditLogORM(Base):
    """
    Audit log - immutable record of all system actions.
    
    Required for regulatory compliance and forensic analysis.
    """
    __tablename__ = "audit_logs"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    
    # Event details
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_action: Mapped[str] = mapped_column(String(32), nullable=False)
    
    # Entity reference
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    
    # Actor
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    
    # Change details
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)
    
    # Context
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id", "created_at"),
        Index("idx_audit_actor", "actor_id", "created_at"),
    )


# =============================================================================
# Pydantic Schemas (API Models)
# =============================================================================

class MoneyAmount(BaseModel):
    """Value object for monetary amounts with validation."""
    
    model_config = ConfigDict(frozen=True)
    
    amount: Decimal = Field(..., ge=Decimal("0.0001"), le=Decimal("999999999999.9999"))
    currency: CurrencyCode
    
    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        """Parse string amounts to Decimal for precision."""
        if isinstance(v, str):
            return Decimal(v)
        return Decimal(str(v))


class WalletCreate(BaseModel):
    """Request schema for creating a wallet."""
    
    external_id: str = Field(..., max_length=64)
    user_id: UUID
    currency: CurrencyCode = CurrencyCode.USD
    wallet_type: WalletType = WalletType.USER
    daily_limit: Decimal | None = Field(None, ge=0)
    monthly_limit: Decimal | None = Field(None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WalletUpdate(BaseModel):
    """Request schema for updating a wallet."""
    
    daily_limit: Decimal | None = Field(None, ge=0)
    monthly_limit: Decimal | None = Field(None, ge=0)
    metadata: dict[str, Any] | None = None


class WalletResponse(BaseModel):
    """Response schema for wallet details."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    external_id: str
    user_id: UUID
    wallet_type: WalletType
    status: WalletStatus
    status_reason: str | None
    currency: CurrencyCode
    daily_limit: Decimal
    monthly_limit: Decimal
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BalanceResponse(BaseModel):
    """Response schema for wallet balance."""
    
    model_config = ConfigDict(from_attributes=True)
    
    wallet_id: UUID
    currency: CurrencyCode
    posted_balance: Decimal
    pending_credits: Decimal
    pending_debits: Decimal
    held_balance: Decimal
    available_balance: Decimal
    as_of: datetime


class CreditRequest(BaseModel):
    """Request schema for crediting a wallet."""
    
    wallet_id: UUID
    amount: Decimal = Field(..., gt=0)
    currency: CurrencyCode
    reference_id: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DebitRequest(BaseModel):
    """Request schema for debiting a wallet."""
    
    wallet_id: UUID
    amount: Decimal = Field(..., gt=0)
    currency: CurrencyCode
    reference_id: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransferRequest(BaseModel):
    """Request schema for wallet-to-wallet transfer."""
    
    source_wallet_id: UUID
    destination_wallet_id: UUID
    amount: Decimal = Field(..., gt=0)
    currency: CurrencyCode
    description: str | None = Field(None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("destination_wallet_id")
    @classmethod
    def validate_different_wallets(cls, v: UUID, info) -> UUID:
        """Ensure source and destination are different."""
        if "source_wallet_id" in info.data and v == info.data["source_wallet_id"]:
            raise ValueError("Cannot transfer to the same wallet")
        return v


class TransactionResponse(BaseModel):
    """Response schema for transaction details."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    idempotency_key: str
    transaction_type: TransactionType
    status: TransactionStatus
    source_wallet_id: UUID | None
    destination_wallet_id: UUID | None
    amount: Decimal
    currency: CurrencyCode
    fee_amount: Decimal
    reference_id: str | None
    description: str | None
    metadata: dict[str, Any]
    failure_reason: str | None
    created_at: datetime
    processed_at: datetime | None


class HoldCreate(BaseModel):
    """Request schema for creating a hold."""
    
    wallet_id: UUID
    amount: Decimal = Field(..., gt=0)
    currency: CurrencyCode
    expires_in_minutes: int = Field(1440, ge=1, le=10080)  # 24h default, max 7 days
    reference_id: str | None = Field(None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HoldResponse(BaseModel):
    """Response schema for hold details."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    wallet_id: UUID
    transaction_id: UUID
    amount: Decimal
    currency: CurrencyCode
    status: HoldStatus
    expires_at: datetime
    created_at: datetime
    resolved_at: datetime | None


class LedgerEntryResponse(BaseModel):
    """Response schema for ledger entry."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    transaction_id: UUID
    wallet_id: UUID
    entry_type: EntryType
    amount: Decimal
    currency: CurrencyCode
    status: EntryStatus
    running_balance: Decimal | None
    created_at: datetime
    posted_at: datetime | None
