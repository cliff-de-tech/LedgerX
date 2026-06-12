"""
LedgerX - API v1 Routes

Defines all API endpoints for the wallet and ledger system.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.domain.models import (
    BalanceResponse,
    CreditRequest,
    CurrencyCode,
    DebitRequest,
    HoldCreate,
    HoldResponse,
    HoldStatus,
    LedgerEntryResponse,
    TransactionResponse,
    TransactionStatus,
    TransactionType,
    TransferRequest,
    WalletCreate,
    WalletResponse,
    WalletStatus,
    WalletUpdate,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class PaginatedResponse(BaseModel):
    """Base paginated response."""

    items: list[Any]
    next_page_token: str | None = None
    total_count: int | None = None


class WalletListResponse(PaginatedResponse):
    """Paginated wallet list."""

    items: list[WalletResponse]


class TransactionListResponse(PaginatedResponse):
    """Paginated transaction list."""

    items: list[TransactionResponse]


class LedgerEntryListResponse(PaginatedResponse):
    """Paginated ledger entry list."""

    items: list[LedgerEntryResponse]
    opening_balance: str | None = None
    closing_balance: str | None = None


class FreezeWalletRequest(BaseModel):
    """Request to freeze a wallet."""

    reason: str = Field(..., max_length=255)


class CaptureHoldRequest(BaseModel):
    """Request to capture a hold."""

    amount: Decimal | None = Field(None, gt=0)


class ReconciliationResponse(BaseModel):
    """Reconciliation result."""

    status: str
    wallets_checked: int
    discrepancies: list[dict[str, Any]]
    completed_at: datetime


# =============================================================================
# Dependency Injection
# =============================================================================


def get_idempotency_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> str:
    """Extract idempotency key from header."""
    return idempotency_key


def get_optional_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    """Extract optional idempotency key from header."""
    return idempotency_key


# =============================================================================
# Wallet Endpoints
# =============================================================================


@router.post(
    "/wallets",
    response_model=WalletResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Wallets"],
    summary="Create a new wallet",
)
async def create_wallet(
    request: WalletCreate,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Create a new wallet for a user.

    Each user can have multiple wallets in different currencies.
    """
    # TODO: Implement with actual service
    return WalletResponse(
        id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        external_id=request.external_id,
        user_id=request.user_id,
        wallet_type=request.wallet_type,
        status=WalletStatus.ACTIVE,
        status_reason=None,
        currency=request.currency,
        daily_limit=request.daily_limit or Decimal("10000"),
        monthly_limit=request.monthly_limit or Decimal("100000"),
        metadata=request.metadata,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/wallets",
    response_model=WalletListResponse,
    tags=["Wallets"],
    summary="List wallets",
)
async def list_wallets(
    user_id: UUID | None = Query(None),
    status: WalletStatus | None = Query(None),
    currency: CurrencyCode | None = Query(None),
    page_size: int = Query(20, ge=1, le=100),
    page_token: str | None = Query(None),
):
    """Retrieve a paginated list of wallets."""
    # TODO: Implement with actual service
    return WalletListResponse(items=[], total_count=0)


@router.get(
    "/wallets/{wallet_id}",
    response_model=WalletResponse,
    tags=["Wallets"],
    summary="Get wallet details",
)
async def get_wallet(wallet_id: UUID):
    """Retrieve detailed information about a specific wallet."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "WALLET_NOT_FOUND", "message": "Wallet not found"}},
    )


@router.patch(
    "/wallets/{wallet_id}",
    response_model=WalletResponse,
    tags=["Wallets"],
    summary="Update wallet",
)
async def update_wallet(
    wallet_id: UUID,
    request: WalletUpdate,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Update wallet settings (limits, metadata)."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "WALLET_NOT_FOUND", "message": "Wallet not found"}},
    )


@router.get(
    "/wallets/{wallet_id}/balance",
    response_model=BalanceResponse,
    tags=["Wallets"],
    summary="Get wallet balance",
)
async def get_wallet_balance(wallet_id: UUID):
    """
    Retrieve the current balance breakdown for a wallet.

    Balance types:
    - **posted_balance**: Settled, confirmed funds
    - **pending_credits**: Incoming funds not yet settled
    - **pending_debits**: Outgoing funds not yet settled
    - **held_balance**: Funds reserved for pending transactions
    - **available_balance**: Funds available for new transactions
    """
    # TODO: Implement with actual service
    return BalanceResponse(
        wallet_id=wallet_id,
        currency=CurrencyCode.USD,
        posted_balance=Decimal("1000.00"),
        pending_credits=Decimal("0"),
        pending_debits=Decimal("0"),
        held_balance=Decimal("0"),
        available_balance=Decimal("1000.00"),
        as_of=datetime.now(timezone.utc),
    )


@router.post(
    "/wallets/{wallet_id}/freeze",
    response_model=WalletResponse,
    tags=["Wallets"],
    summary="Freeze wallet",
)
async def freeze_wallet(
    wallet_id: UUID,
    request: FreezeWalletRequest,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Freeze a wallet to prevent all transactions."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "WALLET_NOT_FOUND", "message": "Wallet not found"}},
    )


@router.post(
    "/wallets/{wallet_id}/unfreeze",
    response_model=WalletResponse,
    tags=["Wallets"],
    summary="Unfreeze wallet",
)
async def unfreeze_wallet(
    wallet_id: UUID,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Unfreeze a previously frozen wallet."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "WALLET_NOT_FOUND", "message": "Wallet not found"}},
    )


# =============================================================================
# Transaction Endpoints
# =============================================================================


@router.post(
    "/transactions/credit",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Transactions"],
    summary="Credit a wallet",
)
async def credit_wallet(
    request: CreditRequest,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Add funds to a wallet (top-up, refund, cashback).

    Creates a single-sided ledger entry crediting the destination wallet.
    """
    # TODO: Implement with actual service
    return TransactionResponse(
        id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        idempotency_key=idempotency_key,
        transaction_type=TransactionType.CREDIT,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=None,
        destination_wallet_id=request.wallet_id,
        amount=request.amount,
        currency=request.currency,
        fee_amount=Decimal("0"),
        reference_id=request.reference_id,
        description=request.description,
        metadata=request.metadata,
        failure_reason=None,
        created_at=datetime.now(timezone.utc),
        processed_at=datetime.now(timezone.utc),
    )


@router.post(
    "/transactions/debit",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Transactions"],
    summary="Debit a wallet",
)
async def debit_wallet(
    request: DebitRequest,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Remove funds from a wallet (payment, withdrawal).

    Fails if available balance is insufficient.
    """
    # TODO: Implement with actual service
    return TransactionResponse(
        id=UUID("550e8400-e29b-41d4-a716-446655440002"),
        idempotency_key=idempotency_key,
        transaction_type=TransactionType.DEBIT,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=request.wallet_id,
        destination_wallet_id=None,
        amount=request.amount,
        currency=request.currency,
        fee_amount=Decimal("0"),
        reference_id=request.reference_id,
        description=request.description,
        metadata=request.metadata,
        failure_reason=None,
        created_at=datetime.now(timezone.utc),
        processed_at=datetime.now(timezone.utc),
    )


@router.post(
    "/transactions/transfer",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Transactions"],
    summary="Transfer between wallets",
)
async def transfer(
    request: TransferRequest,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Transfer funds from one wallet to another.

    This is an atomic operation that creates two ledger entries:
    - DEBIT on source wallet
    - CREDIT on destination wallet
    """
    # TODO: Implement with actual service
    return TransactionResponse(
        id=UUID("550e8400-e29b-41d4-a716-446655440003"),
        idempotency_key=idempotency_key,
        transaction_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=request.source_wallet_id,
        destination_wallet_id=request.destination_wallet_id,
        amount=request.amount,
        currency=request.currency,
        fee_amount=Decimal("0"),
        reference_id=None,
        description=request.description,
        metadata=request.metadata,
        failure_reason=None,
        created_at=datetime.now(timezone.utc),
        processed_at=datetime.now(timezone.utc),
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionResponse,
    tags=["Transactions"],
    summary="Get transaction details",
)
async def get_transaction(transaction_id: UUID):
    """Retrieve details of a specific transaction."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": {
                "code": "TRANSACTION_NOT_FOUND",
                "message": "Transaction not found",
            }
        },
    )


@router.get(
    "/transactions",
    response_model=TransactionListResponse,
    tags=["Transactions"],
    summary="List transactions",
)
async def list_transactions(
    wallet_id: UUID | None = Query(None),
    transaction_type: TransactionType | None = Query(None),
    status: TransactionStatus | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    page_size: int = Query(20, ge=1, le=100),
    page_token: str | None = Query(None),
):
    """Retrieve a paginated list of transactions."""
    # TODO: Implement with actual service
    return TransactionListResponse(items=[], total_count=0)


# =============================================================================
# Hold Endpoints
# =============================================================================


@router.post(
    "/holds",
    response_model=HoldResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Holds"],
    summary="Create a hold",
)
async def create_hold(
    request: HoldCreate,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Reserve funds for a pending transaction.

    Held funds reduce the available balance but don't affect posted balance.
    """
    # TODO: Implement with actual service
    from datetime import timedelta

    return HoldResponse(
        id=UUID("550e8400-e29b-41d4-a716-446655440004"),
        wallet_id=request.wallet_id,
        transaction_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
        amount=request.amount,
        currency=request.currency,
        status=HoldStatus.ACTIVE,
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=request.expires_in_minutes),
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
    )


@router.get(
    "/holds/{hold_id}",
    response_model=HoldResponse,
    tags=["Holds"],
    summary="Get hold details",
)
async def get_hold(hold_id: UUID):
    """Retrieve details of a specific hold."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "HOLD_NOT_FOUND", "message": "Hold not found"}},
    )


@router.post(
    "/holds/{hold_id}/capture",
    response_model=TransactionResponse,
    tags=["Holds"],
    summary="Capture a hold",
)
async def capture_hold(
    hold_id: UUID,
    request: CaptureHoldRequest | None = None,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Convert a hold to an actual debit.

    Can capture the full amount or a partial amount.
    """
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "HOLD_NOT_FOUND", "message": "Hold not found"}},
    )


@router.post(
    "/holds/{hold_id}/release",
    response_model=HoldResponse,
    tags=["Holds"],
    summary="Release a hold",
)
async def release_hold(
    hold_id: UUID,
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Cancel a hold and restore the funds to available balance."""
    # TODO: Implement with actual service
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "HOLD_NOT_FOUND", "message": "Hold not found"}},
    )


# =============================================================================
# Ledger Endpoints
# =============================================================================


@router.get(
    "/ledger/entries",
    response_model=LedgerEntryListResponse,
    tags=["Ledger"],
    summary="List ledger entries",
)
async def list_ledger_entries(
    wallet_id: UUID = Query(...),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    entry_type: str | None = Query(None),
    page_size: int = Query(20, ge=1, le=100),
    page_token: str | None = Query(None),
):
    """Retrieve ledger entries for a wallet (statement)."""
    # TODO: Implement with actual service
    return LedgerEntryListResponse(
        items=[],
        total_count=0,
        opening_balance="0.0000",
        closing_balance="0.0000",
    )


@router.post(
    "/ledger/reconcile",
    response_model=ReconciliationResponse,
    tags=["Ledger"],
    summary="Trigger reconciliation",
)
async def reconcile(
    wallet_id: UUID | None = Query(None),
):
    """
    Verify that cached balances match computed balances from ledger.

    This is typically run as a scheduled job but can be triggered manually.
    """
    # TODO: Implement with actual service
    return ReconciliationResponse(
        status="PASSED",
        wallets_checked=0,
        discrepancies=[],
        completed_at=datetime.now(timezone.utc),
    )
