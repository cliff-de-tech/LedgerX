"""
LedgerX - Ledger Service

Core business logic for double-entry bookkeeping and ledger operations.
This service is the heart of the financial system - all balance changes
flow through the ledger.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConcurrencyError,
    DatabaseError,
    InsufficientFundsError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.domain.models import (
    CurrencyCode,
    EntryStatus,
    EntryType,
    LedgerEntryORM,
    TransactionORM,
    TransactionStatus,
    TransactionType,
    WalletBalanceORM,
    WalletORM,
    WalletStatus,
)


class LedgerService:
    """
    Service for managing ledger entries and balance computations.
    
    Key responsibilities:
    1. Create atomic double-entry journal entries
    2. Compute balances from ledger (source of truth)
    3. Update cached balances atomically
    4. Reconcile cached vs computed balances
    
    All balance-affecting operations MUST go through this service.
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create_transfer_entries(
        self,
        transaction: TransactionORM,
        source_wallet_id: UUID,
        destination_wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
    ) -> tuple[LedgerEntryORM, LedgerEntryORM]:
        """
        Create double-entry for a transfer (debit source, credit destination).
        
        This is atomic - both entries succeed or both fail.
        Balance validation happens BEFORE calling this method.
        
        Args:
            transaction: Parent transaction record
            source_wallet_id: Wallet to debit
            destination_wallet_id: Wallet to credit
            amount: Transfer amount (must be positive)
            currency: Currency code
            
        Returns:
            Tuple of (debit_entry, credit_entry)
            
        Raises:
            InsufficientFundsError: If source has insufficient available balance
        """
        now = datetime.now(timezone.utc)
        
        # Validate and lock source wallet balance
        source_balance = await self._lock_and_validate_balance(
            wallet_id=source_wallet_id,
            required_amount=amount,
            currency=currency
        )
        
        # Create DEBIT entry (money out from source)
        debit_entry = LedgerEntryORM(
            transaction_id=transaction.id,
            wallet_id=source_wallet_id,
            entry_type=EntryType.DEBIT,
            amount=amount,
            currency=currency,
            status=EntryStatus.POSTED,
            posted_at=now,
            running_balance=source_balance.posted_balance - amount
        )
        
        # Create CREDIT entry (money in to destination)
        dest_balance = await self._get_balance(destination_wallet_id)
        credit_entry = LedgerEntryORM(
            transaction_id=transaction.id,
            wallet_id=destination_wallet_id,
            entry_type=EntryType.CREDIT,
            amount=amount,
            currency=currency,
            status=EntryStatus.POSTED,
            posted_at=now,
            running_balance=dest_balance.posted_balance + amount
        )
        
        self.session.add(debit_entry)
        self.session.add(credit_entry)
        
        # Update cached balances atomically
        await self._update_cached_balance(
            wallet_id=source_wallet_id,
            amount=-amount,  # Decrease
            entry=debit_entry
        )
        await self._update_cached_balance(
            wallet_id=destination_wallet_id,
            amount=amount,  # Increase
            entry=credit_entry
        )
        
        return debit_entry, credit_entry
    
    async def create_credit_entry(
        self,
        transaction: TransactionORM,
        wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
    ) -> LedgerEntryORM:
        """
        Create a single credit entry (money in).
        
        Used for: top-ups, refunds, cashback, adjustments.
        
        Args:
            transaction: Parent transaction record
            wallet_id: Wallet to credit
            amount: Credit amount (must be positive)
            currency: Currency code
            
        Returns:
            The created ledger entry
        """
        now = datetime.now(timezone.utc)
        
        # Validate wallet exists and is active
        await self._validate_wallet_active(wallet_id)
        
        balance = await self._get_balance(wallet_id)
        
        entry = LedgerEntryORM(
            transaction_id=transaction.id,
            wallet_id=wallet_id,
            entry_type=EntryType.CREDIT,
            amount=amount,
            currency=currency,
            status=EntryStatus.POSTED,
            posted_at=now,
            running_balance=balance.posted_balance + amount
        )
        
        self.session.add(entry)
        
        await self._update_cached_balance(
            wallet_id=wallet_id,
            amount=amount,
            entry=entry
        )
        
        return entry
    
    async def create_debit_entry(
        self,
        transaction: TransactionORM,
        wallet_id: UUID,
        amount: Decimal,
        currency: CurrencyCode,
    ) -> LedgerEntryORM:
        """
        Create a single debit entry (money out).
        
        Used for: payments, withdrawals, fees.
        
        Args:
            transaction: Parent transaction record
            wallet_id: Wallet to debit
            amount: Debit amount (must be positive)
            currency: Currency code
            
        Returns:
            The created ledger entry
            
        Raises:
            InsufficientFundsError: If wallet has insufficient available balance
        """
        now = datetime.now(timezone.utc)
        
        # Lock and validate balance
        balance = await self._lock_and_validate_balance(
            wallet_id=wallet_id,
            required_amount=amount,
            currency=currency
        )
        
        entry = LedgerEntryORM(
            transaction_id=transaction.id,
            wallet_id=wallet_id,
            entry_type=EntryType.DEBIT,
            amount=amount,
            currency=currency,
            status=EntryStatus.POSTED,
            posted_at=now,
            running_balance=balance.posted_balance - amount
        )
        
        self.session.add(entry)
        
        await self._update_cached_balance(
            wallet_id=wallet_id,
            amount=-amount,
            entry=entry
        )
        
        return entry
    
    async def compute_balance_from_ledger(
        self,
        wallet_id: UUID
    ) -> dict[str, Decimal | int]:
        """
        Compute wallet balance directly from ledger entries.
        
        This is the source of truth, used for reconciliation.
        
        Args:
            wallet_id: Wallet to compute balance for
            
        Returns:
            Dict with computed_balance, credit_sum, debit_sum, entry_count
        """
        query = select(
            func.coalesce(
                func.sum(
                    func.case(
                        (LedgerEntryORM.entry_type == EntryType.CREDIT, LedgerEntryORM.amount),
                        else_=Decimal("0")
                    )
                ),
                Decimal("0")
            ).label("credit_sum"),
            func.coalesce(
                func.sum(
                    func.case(
                        (LedgerEntryORM.entry_type == EntryType.DEBIT, LedgerEntryORM.amount),
                        else_=Decimal("0")
                    )
                ),
                Decimal("0")
            ).label("debit_sum"),
            func.count().label("entry_count")
        ).where(
            LedgerEntryORM.wallet_id == wallet_id,
            LedgerEntryORM.status == EntryStatus.POSTED
        )
        
        result = await self.session.execute(query)
        row = result.one()
        
        credit_sum = row.credit_sum or Decimal("0")
        debit_sum = row.debit_sum or Decimal("0")
        
        return {
            "computed_balance": credit_sum - debit_sum,
            "credit_sum": credit_sum,
            "debit_sum": debit_sum,
            "entry_count": row.entry_count
        }
    
    async def reconcile_wallet(
        self,
        wallet_id: UUID
    ) -> dict[str, any]:
        """
        Reconcile cached balance against computed balance from ledger.
        
        Args:
            wallet_id: Wallet to reconcile
            
        Returns:
            Reconciliation result with status and any discrepancy
        """
        # Get cached balance
        cached = await self._get_balance(wallet_id)
        
        # Compute from ledger
        computed = await self.compute_balance_from_ledger(wallet_id)
        
        discrepancy = cached.posted_balance - computed["computed_balance"]
        
        result = {
            "wallet_id": wallet_id,
            "cached_balance": cached.posted_balance,
            "computed_balance": computed["computed_balance"],
            "discrepancy": discrepancy,
            "entry_count_cached": cached.entry_count,
            "entry_count_computed": computed["entry_count"],
            "status": "PASSED" if discrepancy == Decimal("0") else "FAILED",
            "reconciled_at": datetime.now(timezone.utc)
        }
        
        # Update reconciliation timestamp
        if discrepancy == Decimal("0"):
            cached.reconciled_at = datetime.now(timezone.utc)
        
        return result
    
    async def get_statement(
        self,
        wallet_id: UUID,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> Sequence[LedgerEntryORM]:
        """
        Get ledger entries for a wallet (statement).
        
        Args:
            wallet_id: Wallet to get entries for
            from_date: Start date filter (inclusive)
            to_date: End date filter (inclusive)
            limit: Maximum entries to return
            offset: Pagination offset
            
        Returns:
            List of ledger entries ordered by creation date desc
        """
        query = (
            select(LedgerEntryORM)
            .where(
                LedgerEntryORM.wallet_id == wallet_id,
                LedgerEntryORM.status == EntryStatus.POSTED
            )
            .order_by(LedgerEntryORM.created_at.desc())
        )
        
        if from_date:
            query = query.where(LedgerEntryORM.created_at >= from_date)
        if to_date:
            query = query.where(LedgerEntryORM.created_at <= to_date)
        
        query = query.limit(limit).offset(offset)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    async def _get_balance(self, wallet_id: UUID) -> WalletBalanceORM:
        """Get wallet balance record."""
        query = select(WalletBalanceORM).where(
            WalletBalanceORM.wallet_id == wallet_id
        )
        result = await self.session.execute(query)
        balance = result.scalar_one_or_none()
        
        if not balance:
            raise WalletNotFoundError(wallet_id)
        
        return balance
    
    async def _lock_and_validate_balance(
        self,
        wallet_id: UUID,
        required_amount: Decimal,
        currency: CurrencyCode
    ) -> WalletBalanceORM:
        """
        Lock wallet balance row and validate sufficient funds.
        
        Uses SELECT FOR UPDATE to prevent concurrent modifications.
        """
        # First validate wallet is active
        await self._validate_wallet_active(wallet_id)
        
        # Lock the balance row
        query = (
            select(WalletBalanceORM)
            .where(WalletBalanceORM.wallet_id == wallet_id)
            .with_for_update()
        )
        
        result = await self.session.execute(query)
        balance = result.scalar_one_or_none()
        
        if not balance:
            raise WalletNotFoundError(wallet_id)
        
        # Check available balance (posted - held)
        available = balance.posted_balance - balance.held_balance
        
        if available < required_amount:
            raise InsufficientFundsError(
                wallet_id=wallet_id,
                required=float(required_amount),
                available=float(available)
            )
        
        return balance
    
    async def _validate_wallet_active(self, wallet_id: UUID) -> WalletORM:
        """Validate wallet exists and is active."""
        query = select(WalletORM).where(WalletORM.id == wallet_id)
        result = await self.session.execute(query)
        wallet = result.scalar_one_or_none()
        
        if not wallet:
            raise WalletNotFoundError(wallet_id)
        
        if wallet.status == WalletStatus.FROZEN:
            raise WalletFrozenError(wallet_id)
        
        if wallet.status != WalletStatus.ACTIVE:
            raise WalletFrozenError(wallet_id)  # Use same error for suspended/closed
        
        return wallet
    
    async def _update_cached_balance(
        self,
        wallet_id: UUID,
        amount: Decimal,
        entry: LedgerEntryORM
    ) -> None:
        """
        Update cached balance atomically with optimistic locking.
        
        The balance trigger in PostgreSQL handles the actual update,
        but we do it in application code for better control and testing.
        """
        query = select(WalletBalanceORM).where(
            WalletBalanceORM.wallet_id == wallet_id
        )
        result = await self.session.execute(query)
        balance = result.scalar_one_or_none()
        
        if not balance:
            raise WalletNotFoundError(wallet_id)
        
        # Update balance
        balance.posted_balance = balance.posted_balance + amount
        balance.last_entry_id = entry.id
        balance.last_entry_at = entry.posted_at
        balance.entry_count = balance.entry_count + 1
        balance.version = balance.version + 1
