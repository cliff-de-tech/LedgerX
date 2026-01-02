"""
LedgerX - Wallet Service

Service for wallet lifecycle management and balance queries.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    DuplicateWalletError,
    WalletNotFoundError,
    WalletStateError,
)
from app.domain.models import (
    CurrencyCode,
    WalletBalanceORM,
    WalletCreate,
    WalletORM,
    WalletStatus,
    WalletType,
    WalletUpdate,
)


class WalletService:
    """
    Service for wallet management operations.
    
    Responsibilities:
    1. Wallet lifecycle (create, freeze, close)
    2. Balance queries (via cached balance)
    3. Wallet configuration updates
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create_wallet(
        self,
        data: WalletCreate,
        actor_id: UUID | None = None,
    ) -> WalletORM:
        """
        Create a new wallet.
        
        Args:
            data: Wallet creation data
            actor_id: User creating the wallet
            
        Returns:
            Created wallet
            
        Raises:
            DuplicateWalletError: If external_id already exists
        """
        # Check for existing wallet with same external_id
        existing = await self._get_wallet_by_external_id(data.external_id)
        if existing:
            raise DuplicateWalletError(data.external_id)
        
        # Create wallet
        wallet = WalletORM(
            external_id=data.external_id,
            user_id=data.user_id,
            wallet_type=data.wallet_type,
            currency=data.currency,
            daily_limit=data.daily_limit or Decimal("10000.0000"),
            monthly_limit=data.monthly_limit or Decimal("100000.0000"),
            metadata_=data.metadata,
            created_by=actor_id,
        )
        self.session.add(wallet)
        await self.session.flush()
        
        # Create balance record
        balance = WalletBalanceORM(wallet_id=wallet.id)
        self.session.add(balance)
        
        return wallet
    
    async def get_wallet(self, wallet_id: UUID) -> WalletORM:
        """
        Get wallet by ID.
        
        Args:
            wallet_id: Wallet ID
            
        Returns:
            Wallet record
            
        Raises:
            WalletNotFoundError: If wallet doesn't exist
        """
        query = select(WalletORM).where(WalletORM.id == wallet_id)
        result = await self.session.execute(query)
        wallet = result.scalar_one_or_none()
        
        if not wallet:
            raise WalletNotFoundError(wallet_id)
        
        return wallet
    
    async def get_wallet_by_external_id(self, external_id: str) -> WalletORM:
        """
        Get wallet by external ID.
        
        Args:
            external_id: Client-provided wallet ID
            
        Returns:
            Wallet record
            
        Raises:
            WalletNotFoundError: If wallet doesn't exist
        """
        wallet = await self._get_wallet_by_external_id(external_id)
        if not wallet:
            raise WalletNotFoundError(external_id)
        return wallet
    
    async def list_wallets(
        self,
        user_id: UUID | None = None,
        status: WalletStatus | None = None,
        currency: CurrencyCode | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[WalletORM]:
        """
        List wallets with optional filters.
        
        Args:
            user_id: Filter by user
            status: Filter by status
            currency: Filter by currency
            limit: Max results
            offset: Pagination offset
            
        Returns:
            List of wallets
        """
        query = select(WalletORM)
        
        if user_id:
            query = query.where(WalletORM.user_id == user_id)
        if status:
            query = query.where(WalletORM.status == status)
        if currency:
            query = query.where(WalletORM.currency == currency)
        
        query = query.order_by(WalletORM.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_balance(self, wallet_id: UUID) -> WalletBalanceORM:
        """
        Get wallet balance.
        
        Args:
            wallet_id: Wallet ID
            
        Returns:
            Balance record with all balance fields
            
        Raises:
            WalletNotFoundError: If wallet doesn't exist
        """
        query = select(WalletBalanceORM).where(
            WalletBalanceORM.wallet_id == wallet_id
        )
        result = await self.session.execute(query)
        balance = result.scalar_one_or_none()
        
        if not balance:
            raise WalletNotFoundError(wallet_id)
        
        return balance
    
    async def update_wallet(
        self,
        wallet_id: UUID,
        data: WalletUpdate,
        actor_id: UUID | None = None,
    ) -> WalletORM:
        """
        Update wallet settings.
        
        Args:
            wallet_id: Wallet to update
            data: Update data
            actor_id: User making the update
            
        Returns:
            Updated wallet
        """
        wallet = await self.get_wallet(wallet_id)
        
        if data.daily_limit is not None:
            wallet.daily_limit = data.daily_limit
        if data.monthly_limit is not None:
            wallet.monthly_limit = data.monthly_limit
        if data.metadata is not None:
            wallet.metadata_ = data.metadata
        
        wallet.version += 1
        
        return wallet
    
    async def freeze_wallet(
        self,
        wallet_id: UUID,
        reason: str,
        actor_id: UUID | None = None,
    ) -> WalletORM:
        """
        Freeze a wallet to prevent transactions.
        
        Args:
            wallet_id: Wallet to freeze
            reason: Reason for freezing
            actor_id: User freezing the wallet
            
        Returns:
            Updated wallet
            
        Raises:
            WalletStateError: If wallet is not active
        """
        wallet = await self.get_wallet(wallet_id)
        
        if wallet.status != WalletStatus.ACTIVE:
            raise WalletStateError(
                wallet_id=wallet_id,
                current_state=wallet.status.value,
                required_state=WalletStatus.ACTIVE.value
            )
        
        wallet.status = WalletStatus.FROZEN
        wallet.status_reason = reason
        wallet.status_updated_at = datetime.now(timezone.utc)
        wallet.version += 1
        
        return wallet
    
    async def unfreeze_wallet(
        self,
        wallet_id: UUID,
        actor_id: UUID | None = None,
    ) -> WalletORM:
        """
        Unfreeze a wallet to allow transactions.
        
        Args:
            wallet_id: Wallet to unfreeze
            actor_id: User unfreezing the wallet
            
        Returns:
            Updated wallet
            
        Raises:
            WalletStateError: If wallet is not frozen
        """
        wallet = await self.get_wallet(wallet_id)
        
        if wallet.status != WalletStatus.FROZEN:
            raise WalletStateError(
                wallet_id=wallet_id,
                current_state=wallet.status.value,
                required_state=WalletStatus.FROZEN.value
            )
        
        wallet.status = WalletStatus.ACTIVE
        wallet.status_reason = None
        wallet.status_updated_at = datetime.now(timezone.utc)
        wallet.version += 1
        
        return wallet
    
    async def close_wallet(
        self,
        wallet_id: UUID,
        actor_id: UUID | None = None,
    ) -> WalletORM:
        """
        Close a wallet (soft delete).
        
        Wallet must have zero balance.
        
        Args:
            wallet_id: Wallet to close
            actor_id: User closing the wallet
            
        Returns:
            Updated wallet
            
        Raises:
            WalletStateError: If wallet has non-zero balance
        """
        wallet = await self.get_wallet(wallet_id)
        balance = await self.get_balance(wallet_id)
        
        if balance.posted_balance != Decimal("0") or balance.held_balance != Decimal("0"):
            raise WalletStateError(
                wallet_id=wallet_id,
                current_state="has_balance",
                required_state="zero_balance"
            )
        
        wallet.status = WalletStatus.CLOSED
        wallet.status_reason = "Closed by user"
        wallet.status_updated_at = datetime.now(timezone.utc)
        wallet.version += 1
        
        return wallet
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    async def _get_wallet_by_external_id(
        self,
        external_id: str
    ) -> WalletORM | None:
        """Get wallet by external ID (internal use)."""
        query = select(WalletORM).where(WalletORM.external_id == external_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
