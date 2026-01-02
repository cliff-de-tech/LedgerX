"""
LedgerX - Custom Exception Classes

Defines domain-specific exceptions with error codes for consistent API responses.
"""

from typing import Any
from uuid import UUID


class LedgerXException(Exception):
    """Base exception for all LedgerX errors."""
    
    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred"
    
    def __init__(
        self,
        message: str | None = None,
        details: list[dict[str, Any]] | None = None,
        **kwargs: Any
    ):
        self.message = message or self.message
        self.details = details or []
        self.extra = kwargs
        super().__init__(self.message)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert exception to API error response format."""
        error = {
            "code": self.error_code,
            "message": self.message,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


# =============================================================================
# Validation Errors (4xx)
# =============================================================================

class ValidationError(LedgerXException):
    """Request validation failed."""
    error_code = "VALIDATION_ERROR"
    status_code = 400
    message = "Request validation failed"


class InvalidAmountError(ValidationError):
    """Invalid transaction amount."""
    error_code = "INVALID_AMOUNT"
    message = "Invalid transaction amount"
    
    def __init__(self, amount: Any, reason: str = "Amount must be positive"):
        super().__init__(
            message=f"Invalid amount: {amount}. {reason}",
            details=[{"field": "amount", "message": reason}]
        )


class InvalidCurrencyError(ValidationError):
    """Invalid or mismatched currency."""
    error_code = "INVALID_CURRENCY"
    message = "Invalid currency code"


class CurrencyMismatchError(ValidationError):
    """Currency mismatch between wallets."""
    error_code = "CURRENCY_MISMATCH"
    message = "Source and destination wallets must have the same currency"


# =============================================================================
# Authentication & Authorization Errors
# =============================================================================

class AuthenticationError(LedgerXException):
    """Authentication failed."""
    error_code = "AUTHENTICATION_FAILED"
    status_code = 401
    message = "Authentication required"


class AuthorizationError(LedgerXException):
    """Authorization failed - insufficient permissions."""
    error_code = "AUTHORIZATION_FAILED"
    status_code = 403
    message = "Insufficient permissions for this operation"


class InvalidTokenError(AuthenticationError):
    """Invalid or expired JWT token."""
    error_code = "INVALID_TOKEN"
    message = "Invalid or expired authentication token"


# =============================================================================
# Resource Not Found Errors
# =============================================================================

class NotFoundError(LedgerXException):
    """Resource not found."""
    error_code = "NOT_FOUND"
    status_code = 404
    message = "Resource not found"


class WalletNotFoundError(NotFoundError):
    """Wallet not found."""
    error_code = "WALLET_NOT_FOUND"
    message = "Wallet not found"
    
    def __init__(self, wallet_id: UUID | str):
        super().__init__(message=f"Wallet not found: {wallet_id}")


class TransactionNotFoundError(NotFoundError):
    """Transaction not found."""
    error_code = "TRANSACTION_NOT_FOUND"
    message = "Transaction not found"
    
    def __init__(self, transaction_id: UUID | str):
        super().__init__(message=f"Transaction not found: {transaction_id}")


class HoldNotFoundError(NotFoundError):
    """Hold not found."""
    error_code = "HOLD_NOT_FOUND"
    message = "Hold not found"
    
    def __init__(self, hold_id: UUID | str):
        super().__init__(message=f"Hold not found: {hold_id}")


# =============================================================================
# Conflict Errors
# =============================================================================

class ConflictError(LedgerXException):
    """Resource conflict."""
    error_code = "CONFLICT"
    status_code = 409
    message = "Resource conflict"


class DuplicateWalletError(ConflictError):
    """Wallet with external_id already exists."""
    error_code = "DUPLICATE_WALLET"
    message = "Wallet with this external ID already exists"
    
    def __init__(self, external_id: str):
        super().__init__(message=f"Wallet already exists: {external_id}")


class IdempotencyConflictError(ConflictError):
    """Same idempotency key with different request body."""
    error_code = "IDEMPOTENCY_CONFLICT"
    message = "Idempotency key reused with different request parameters"


class WalletStateError(ConflictError):
    """Wallet is in invalid state for operation."""
    error_code = "WALLET_STATE_ERROR"
    message = "Wallet is in invalid state for this operation"
    
    def __init__(self, wallet_id: UUID | str, current_state: str, required_state: str):
        super().__init__(
            message=f"Wallet {wallet_id} is {current_state}, must be {required_state}"
        )


class HoldStateError(ConflictError):
    """Hold is in invalid state for operation."""
    error_code = "HOLD_STATE_ERROR"
    message = "Hold is in invalid state for this operation"
    
    def __init__(self, hold_id: UUID | str, current_state: str):
        super().__init__(
            message=f"Hold {hold_id} is {current_state}, cannot be modified"
        )


class TransactionAlreadyProcessedError(ConflictError):
    """Transaction was already processed (idempotency hit)."""
    error_code = "TRANSACTION_ALREADY_PROCESSED"
    message = "Transaction was already processed"
    
    def __init__(self, idempotency_key: str, transaction_id: UUID):
        self.transaction_id = transaction_id
        super().__init__(
            message=f"Transaction already processed with key: {idempotency_key}"
        )


# =============================================================================
# Business Logic Errors
# =============================================================================

class InsufficientFundsError(LedgerXException):
    """Insufficient balance for transaction."""
    error_code = "INSUFFICIENT_FUNDS"
    status_code = 402  # Payment Required
    message = "Insufficient funds for this transaction"
    
    def __init__(
        self,
        wallet_id: UUID | str,
        required: float,
        available: float
    ):
        self.required = required
        self.available = available
        super().__init__(
            message=f"Insufficient funds. Required: {required:.4f}, Available: {available:.4f}",
            details=[{
                "field": "amount",
                "message": f"Required: {required:.4f}, Available: {available:.4f}"
            }]
        )


class DailyLimitExceededError(LedgerXException):
    """Daily transaction limit exceeded."""
    error_code = "DAILY_LIMIT_EXCEEDED"
    status_code = 422
    message = "Daily transaction limit exceeded"
    
    def __init__(self, wallet_id: UUID | str, limit: float, attempted: float):
        super().__init__(
            message=f"Daily limit exceeded. Limit: {limit:.4f}, Attempted: {attempted:.4f}"
        )


class MonthlyLimitExceededError(LedgerXException):
    """Monthly transaction limit exceeded."""
    error_code = "MONTHLY_LIMIT_EXCEEDED"
    status_code = 422
    message = "Monthly transaction limit exceeded"


class SameWalletTransferError(ValidationError):
    """Cannot transfer to the same wallet."""
    error_code = "SAME_WALLET_TRANSFER"
    message = "Cannot transfer funds to the same wallet"


class WalletFrozenError(LedgerXException):
    """Wallet is frozen and cannot process transactions."""
    error_code = "WALLET_FROZEN"
    status_code = 422
    message = "Wallet is frozen and cannot process transactions"
    
    def __init__(self, wallet_id: UUID | str):
        super().__init__(message=f"Wallet {wallet_id} is frozen")


class HoldExpiredError(LedgerXException):
    """Hold has expired."""
    error_code = "HOLD_EXPIRED"
    status_code = 422
    message = "Hold has expired and cannot be captured"


# =============================================================================
# System Errors
# =============================================================================

class DatabaseError(LedgerXException):
    """Database operation failed."""
    error_code = "DATABASE_ERROR"
    status_code = 500
    message = "Database operation failed"


class CacheError(LedgerXException):
    """Cache operation failed (non-critical)."""
    error_code = "CACHE_ERROR"
    status_code = 500
    message = "Cache operation failed"


class ConcurrencyError(LedgerXException):
    """Optimistic locking conflict."""
    error_code = "CONCURRENCY_ERROR"
    status_code = 409
    message = "Concurrent modification detected, please retry"


class ServiceUnavailableError(LedgerXException):
    """External service unavailable."""
    error_code = "SERVICE_UNAVAILABLE"
    status_code = 503
    message = "Service temporarily unavailable"


# =============================================================================
# Rate Limiting
# =============================================================================

class RateLimitExceededError(LedgerXException):
    """Rate limit exceeded."""
    error_code = "RATE_LIMIT_EXCEEDED"
    status_code = 429
    message = "Rate limit exceeded, please try again later"
    
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(
            message=f"Rate limit exceeded. Retry after {retry_after} seconds"
        )
