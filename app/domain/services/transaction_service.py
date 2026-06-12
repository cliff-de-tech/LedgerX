"""
LedgerX - Transaction Service

Orchestrates financial transactions with idempotency, atomic execution,
and proper error handling. Acts as the facade for all transaction operations.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    CurrencyMismatchError,
    HoldExpiredError,
    HoldNotFoundError,
    HoldStateError,
    IdempotencyConflictError,
    InsufficientFundsError,
    InvalidAmountError,
    SameWalletTransferError,
    TransactionNotFoundError,
    ValidationError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.domain.models import (
    CurrencyCode,
    HoldORM,
    HoldStatus,
    IdempotencyKeyORM,
    TransactionORM,
    TransactionStatus,
    TransactionType,
    WalletORM,
    WalletStatus,
)
from app.domain.services.ledger_service import LedgerService


class TransactionService:
    """
    Service for orchestrating financial transactions.

    Key responsibilities:
    1. Idempotency handling - prevent duplicate processing
    2. Transaction lifecycle management
    3. Hold/capture/release flows
    4. Coordination with LedgerService for actual balance changes

    All public methods are idempotent and safe to retry.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.ledger_service = LedgerService(session)

    async def credit(
        self,
        idempotency_key: str,
        wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
        reference_id: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: UUID | None = None,
    ) -> TransactionORM:
        """
        Credit funds to a wallet (top-up, refund, cashback).

        Args:
            idempotency_key: Unique key for deduplication
            wallet_id: Destination wallet
            amount: Amount to credit (must be positive)
            currency: Currency code
            reference_id: External reference (e.g., bank transfer ID)
            description: Human-readable description
            metadata: Additional metadata
            actor_id: User/system initiating the transaction

        Returns:
            Transaction record

        Raises:
            WalletNotFoundError: If wallet doesn't exist
            WalletFrozenError: If wallet is frozen/suspended
            IdempotencyConflictError: If key reused with different params
        """
        # Check idempotency
        existing = await self._check_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "credit",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
        )
        if existing:
            return existing
        
        # Validate wallet
        wallet = await self._get_active_wallet(wallet_id)

        # Validate currency match
        if wallet.currency != currency:
            raise CurrencyMismatchError(
                f"Wallet currency is {wallet.currency.value}, got {currency.value}"
            )

        # Create transaction record
        transaction = TransactionORM(
            idempotency_key=idempotency_key,
            transaction_type=TransactionType.CREDIT,
            status=TransactionStatus.PROCESSING,
            destination_wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            reference_id=reference_id,
            description=description,
            metadata_=metadata or {},
            created_by=actor_id,
        )
        self.session.add(transaction)
        await self.session.flush()

        # Create ledger entry
        await self.ledger_service.create_credit_entry(
            transaction=transaction,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
        )

        # Complete transaction
        transaction.status = TransactionStatus.COMPLETED
        transaction.processed_at = datetime.now(timezone.utc)

        # Store idempotency record
        await self._store_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "credit",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
            transaction_id=transaction.id,
        )

        return transaction

    async def debit(
        self,
        idempotency_key: str,
        wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
        reference_id: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: UUID | None = None,
    ) -> TransactionORM:
        """
        Debit funds from a wallet (payment, withdrawal).

        Args:
            idempotency_key: Unique key for deduplication
            wallet_id: Source wallet
            amount: Amount to debit (must be positive)
            currency: Currency code
            reference_id: External reference
            description: Human-readable description
            metadata: Additional metadata
            actor_id: User/system initiating the transaction

        Returns:
            Transaction record

        Raises:
            WalletNotFoundError: If wallet doesn't exist
            WalletFrozenError: If wallet is frozen/suspended
            InsufficientFundsError: If balance is insufficient
            IdempotencyConflictError: If key reused with different params
        """
        # Check idempotency
        existing = await self._check_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "debit",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
        )
        if existing:
            return existing
        
        # Validate wallet
        wallet = await self._get_active_wallet(wallet_id)

        # Validate currency match
        if wallet.currency != currency:
            raise CurrencyMismatchError(
                f"Wallet currency is {wallet.currency.value}, got {currency.value}"
            )

        # Create transaction record
        transaction = TransactionORM(
            idempotency_key=idempotency_key,
            transaction_type=TransactionType.DEBIT,
            status=TransactionStatus.PROCESSING,
            source_wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            reference_id=reference_id,
            description=description,
            metadata_=metadata or {},
            created_by=actor_id,
        )
        self.session.add(transaction)
        await self.session.flush()

        try:
            # Create ledger entry (validates balance)
            await self.ledger_service.create_debit_entry(
                transaction=transaction,
                wallet_id=wallet_id,
                amount=amount,
                currency=currency,
            )

            # Complete transaction
            transaction.status = TransactionStatus.COMPLETED
            transaction.processed_at = datetime.now(timezone.utc)

        except InsufficientFundsError:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = "Insufficient funds"
            raise

        # Store idempotency record
        await self._store_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "debit",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
            transaction_id=transaction.id,
        )

        return transaction

    async def transfer(
        self,
        idempotency_key: str,
        source_wallet_id: UUID,
        destination_wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: UUID | None = None,
    ) -> TransactionORM:
        """
        Transfer funds between wallets (P2P, merchant payment).

        This is an atomic double-entry operation:
        - DEBIT source wallet
        - CREDIT destination wallet

        Both succeed or both fail.

        Args:
            idempotency_key: Unique key for deduplication
            source_wallet_id: Wallet to debit
            destination_wallet_id: Wallet to credit
            amount: Amount to transfer (must be positive)
            currency: Currency code
            description: Human-readable description
            metadata: Additional metadata
            actor_id: User/system initiating the transaction

        Returns:
            Transaction record

        Raises:
            SameWalletTransferError: If source == destination
            WalletNotFoundError: If either wallet doesn't exist
            WalletFrozenError: If either wallet is frozen/suspended
            InsufficientFundsError: If source balance is insufficient
            CurrencyMismatchError: If wallets have different currencies
        """
        # Validate different wallets
        if source_wallet_id == destination_wallet_id:
            raise SameWalletTransferError()

        # Check idempotency
        existing = await self._check_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "transfer",
                "source_wallet_id": str(source_wallet_id),
                "destination_wallet_id": str(destination_wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
        )
        if existing:
            return existing
        
        # Validate both wallets
        source_wallet = await self._get_active_wallet(source_wallet_id)
        dest_wallet = await self._get_active_wallet(destination_wallet_id)

        # Validate currency match
        if source_wallet.currency != currency or dest_wallet.currency != currency:
            raise CurrencyMismatchError(f"Both wallets must use {currency.value}")

        # Create transaction record
        transaction = TransactionORM(
            idempotency_key=idempotency_key,
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.PROCESSING,
            source_wallet_id=source_wallet_id,
            destination_wallet_id=destination_wallet_id,
            amount=amount,
            currency=currency,
            description=description,
            metadata_=metadata or {},
            created_by=actor_id,
        )
        self.session.add(transaction)
        await self.session.flush()

        try:
            # Create double-entry (validates source balance)
            await self.ledger_service.create_transfer_entries(
                transaction=transaction,
                source_wallet_id=source_wallet_id,
                destination_wallet_id=destination_wallet_id,
                amount=amount,
                currency=currency,
            )

            # Complete transaction
            transaction.status = TransactionStatus.COMPLETED
            transaction.processed_at = datetime.now(timezone.utc)

        except InsufficientFundsError:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = "Insufficient funds"
            raise

        # Store idempotency record
        await self._store_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "transfer",
                "source_wallet_id": str(source_wallet_id),
                "destination_wallet_id": str(destination_wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
            transaction_id=transaction.id,
        )

        return transaction

    async def create_hold(
        self,
        idempotency_key: str,
        wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
        expires_in_minutes: int = settings.DEFAULT_HOLD_EXPIRY_MINUTES,
        reference_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: UUID | None = None,
    ) -> tuple[TransactionORM, HoldORM]:
        """
        Create a hold on wallet funds.

        Holds reduce available balance without affecting posted balance.
        They must be captured or released before expiry.

        Args:
            idempotency_key: Unique key for deduplication
            wallet_id: Wallet to hold funds on
            amount: Amount to hold
            currency: Currency code
            expires_in_minutes: Hold expiration (default 24 hours)
            reference_id: External reference
            metadata: Additional metadata
            actor_id: User/system initiating the hold

        Returns:
            Tuple of (transaction, hold)
        """
        # Check idempotency
        existing = await self._check_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "hold",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
        )
        if existing:
            # Get the hold for this transaction
            hold_query = select(HoldORM).where(HoldORM.transaction_id == existing.id)
            result = await self.session.execute(hold_query)
            hold = result.scalar_one()
            return existing, hold
        
        # Validate wallet
        wallet = await self._get_active_wallet(wallet_id)

        if wallet.currency != currency:
            raise CurrencyMismatchError(
                f"Wallet currency is {wallet.currency.value}, got {currency.value}"
            )

        # Validate available balance
        from app.domain.services.wallet_service import WalletService

        wallet_service = WalletService(self.session)
        balance = await wallet_service.get_balance(wallet_id)

        if balance.available_balance < amount:
            raise InsufficientFundsError(
                wallet_id=wallet_id,
                required=float(amount),
                available=float(balance.available_balance),
            )

        # Create transaction
        transaction = TransactionORM(
            idempotency_key=idempotency_key,
            transaction_type=TransactionType.HOLD,
            status=TransactionStatus.COMPLETED,
            source_wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            reference_id=reference_id,
            metadata_=metadata or {},
            created_by=actor_id,
            processed_at=datetime.now(timezone.utc),
        )
        self.session.add(transaction)
        await self.session.flush()

        # Create hold record
        hold = HoldORM(
            wallet_id=wallet_id,
            transaction_id=transaction.id,
            amount=amount,
            currency=currency,
            status=HoldStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=expires_in_minutes),
        )
        self.session.add(hold)

        # Update held balance
        balance.held_balance = balance.held_balance + amount

        await self._store_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "hold",
                "wallet_id": str(wallet_id),
                "amount": str(amount),
                "currency": currency.value,
            },
            transaction_id=transaction.id,
        )

        return transaction, hold

    async def capture_hold(
        self,
        idempotency_key: str,
        hold_id: UUID,
        amount: Decimal | None = None,
        actor_id: UUID | None = None,
    ) -> TransactionORM:
        """
        Capture a hold, converting it to an actual debit.

        Can capture full amount or partial. Remaining is released.

        Args:
            idempotency_key: Unique key for deduplication
            hold_id: Hold to capture
            amount: Amount to capture (defaults to full hold amount)
            actor_id: User/system initiating the capture

        Returns:
            Capture transaction

        Raises:
            HoldNotFoundError: If hold doesn't exist
            HoldStateError: If hold is not active
            HoldExpiredError: If hold has expired
        """
        # Get hold
        hold = await self._get_hold(hold_id)

        if hold.status != HoldStatus.ACTIVE:
            raise HoldStateError(hold_id, hold.status.value)

        if hold.expires_at < datetime.now(timezone.utc):
            hold.status = HoldStatus.EXPIRED
            raise HoldExpiredError()

        capture_amount = amount or hold.amount

        # Check idempotency
        existing = await self._check_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "capture",
                "hold_id": str(hold_id),
                "amount": str(capture_amount),
            },
        )
        if existing:
            return existing
        
        # Create capture transaction
        transaction = TransactionORM(
            idempotency_key=idempotency_key,
            transaction_type=TransactionType.CAPTURE,
            status=TransactionStatus.PROCESSING,
            source_wallet_id=hold.wallet_id,
            amount=capture_amount,
            currency=hold.currency,
            parent_transaction_id=hold.transaction_id,
            created_by=actor_id,
        )
        self.session.add(transaction)
        await self.session.flush()

        # Create debit entry (from held funds, not available)
        await self.ledger_service.create_debit_entry(
            transaction=transaction,
            wallet_id=hold.wallet_id,
            amount=capture_amount,
            currency=hold.currency,
        )

        # Update hold status
        hold.status = HoldStatus.CAPTURED
        hold.resolved_at = datetime.now(timezone.utc)
        hold.resolved_transaction_id = transaction.id

        # Release held balance
        from app.domain.services.wallet_service import WalletService

        wallet_service = WalletService(self.session)
        balance = await wallet_service.get_balance(hold.wallet_id)
        balance.held_balance = balance.held_balance - hold.amount

        transaction.status = TransactionStatus.COMPLETED
        transaction.processed_at = datetime.now(timezone.utc)

        await self._store_idempotency(
            idempotency_key=idempotency_key,
            request_data={
                "type": "capture",
                "hold_id": str(hold_id),
                "amount": str(capture_amount),
            },
            transaction_id=transaction.id,
        )

        return transaction

    async def release_hold(
        self,
        idempotency_key: str,
        hold_id: UUID,
        actor_id: UUID | None = None,
    ) -> HoldORM:
        """
        Release a hold, restoring funds to available balance.

        Args:
            idempotency_key: Unique key for deduplication
            hold_id: Hold to release
            actor_id: User/system initiating the release

        Returns:
            Updated hold record
        """
        # Get hold
        hold = await self._get_hold(hold_id)

        if hold.status != HoldStatus.ACTIVE:
            raise HoldStateError(hold_id, hold.status.value)

        # Update hold status
        hold.status = HoldStatus.RELEASED
        hold.resolved_at = datetime.now(timezone.utc)

        # Release held balance
        from app.domain.services.wallet_service import WalletService

        wallet_service = WalletService(self.session)
        balance = await wallet_service.get_balance(hold.wallet_id)
        balance.held_balance = balance.held_balance - hold.amount

        return hold

    async def get_transaction(self, transaction_id: UUID) -> TransactionORM:
        """Get a transaction by ID."""
        query = select(TransactionORM).where(TransactionORM.id == transaction_id)
        result = await self.session.execute(query)
        transaction = result.scalar_one_or_none()

        if not transaction:
            raise TransactionNotFoundError(transaction_id)

        return transaction

    async def get_transaction_by_idempotency_key(
        self, idempotency_key: str
    ) -> TransactionORM | None:
        """Get a transaction by idempotency key."""
        query = select(TransactionORM).where(
            TransactionORM.idempotency_key == idempotency_key
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # =========================================================================
    # Private Methods
    # =========================================================================

    async def _get_active_wallet(self, wallet_id: UUID) -> WalletORM:
        """Get wallet and validate it's active."""
        query = select(WalletORM).where(WalletORM.id == wallet_id)
        result = await self.session.execute(query)
        wallet = result.scalar_one_or_none()

        if not wallet:
            raise WalletNotFoundError(wallet_id)

        if wallet.status == WalletStatus.FROZEN:
            raise WalletFrozenError(wallet_id)

        if wallet.status != WalletStatus.ACTIVE:
            raise WalletFrozenError(wallet_id)

        return wallet

    async def _get_hold(self, hold_id: UUID) -> HoldORM:
        """Get hold by ID."""
        query = select(HoldORM).where(HoldORM.id == hold_id)
        result = await self.session.execute(query)
        hold = result.scalar_one_or_none()

        if not hold:
            raise HoldNotFoundError(hold_id)

        return hold

    async def _check_idempotency(
        self, idempotency_key: str, request_data: dict[str, Any]
    ) -> TransactionORM | None:
        """
        Check if request was already processed.

        Returns existing transaction if found with same key and request hash.
        Raises IdempotencyConflictError if key reused with different data.
        """
        query = select(IdempotencyKeyORM).where(
            IdempotencyKeyORM.key == idempotency_key
        )
        result = await self.session.execute(query)
        existing = result.scalar_one_or_none()

        if not existing:
            return None

        # Verify request hash matches
        request_hash = self._hash_request(request_data)
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError()

        # Return existing transaction
        if existing.transaction_id:
            return await self.get_transaction(existing.transaction_id)

        return None

    async def _store_idempotency(
        self, idempotency_key: str, request_data: dict[str, Any], transaction_id: UUID
    ) -> None:
        """Store idempotency key for future deduplication."""
        idempotency_record = IdempotencyKeyORM(
            key=idempotency_key,
            request_hash=self._hash_request(request_data),
            transaction_id=transaction_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.CACHE_TTL_IDEMPOTENCY),
        )
        self.session.add(idempotency_record)

    @staticmethod
    def _hash_request(data: dict[str, Any]) -> str:
        """Create deterministic hash of request data."""
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        """Validate transaction amount against configured limits."""
        min_amount = Decimal(str(settings.MIN_TRANSACTION_AMOUNT))
        max_amount = Decimal(str(settings.MAX_TRANSACTION_AMOUNT))
        
        if amount <= 0:
            raise InvalidAmountError(amount, reason="Amount must be positive")
        if amount < min_amount:
            raise InvalidAmountError(
                amount,
                reason=f"Amount must be at least {min_amount}"
            )
        if amount > max_amount:
            raise InvalidAmountError(
                amount,
                reason=f"Amount must be at most {max_amount}"
            )

    @staticmethod
    def _validate_hold_expiry(expires_in_minutes: int) -> None:
        """Validate hold expiry duration."""
        max_expiry = settings.MAX_HOLD_EXPIRY_MINUTES
        if expires_in_minutes < 1 or expires_in_minutes > max_expiry:
            raise ValidationError(
                message="Invalid hold expiry duration",
                details=[{
                    "field": "expires_in_minutes",
                    "message": f"Expiry must be between 1 and {max_expiry} minutes"
                }]
            )

    @staticmethod
    def _validate_capture_amount(
        capture_amount: Decimal,
        held_amount: Decimal
    ) -> None:
        """Ensure capture amount does not exceed held amount."""
        if capture_amount > held_amount:
            raise InvalidAmountError(
                capture_amount,
                reason=f"Capture amount exceeds held amount of {held_amount}"
            )
