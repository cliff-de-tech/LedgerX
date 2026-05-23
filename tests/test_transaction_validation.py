from decimal import Decimal

import pytest

from app.core.config import settings
from app.core.exceptions import InvalidAmountError, ValidationError
from app.domain.services.transaction_service import TransactionService


def test_validate_amount_rejects_below_min():
    min_amount = Decimal(str(settings.MIN_TRANSACTION_AMOUNT))
    too_small = min_amount - Decimal("0.0001")
    with pytest.raises(InvalidAmountError):
        TransactionService._validate_amount(too_small)


def test_validate_amount_rejects_above_max():
    max_amount = Decimal(str(settings.MAX_TRANSACTION_AMOUNT))
    too_large = max_amount + Decimal("1")
    with pytest.raises(InvalidAmountError):
        TransactionService._validate_amount(too_large)


def test_validate_amount_accepts_bounds():
    min_amount = Decimal(str(settings.MIN_TRANSACTION_AMOUNT))
    max_amount = Decimal(str(settings.MAX_TRANSACTION_AMOUNT))
    TransactionService._validate_amount(min_amount)
    TransactionService._validate_amount(max_amount)


def test_validate_hold_expiry_rejects_out_of_range():
    with pytest.raises(ValidationError):
        TransactionService._validate_hold_expiry(
            settings.MAX_HOLD_EXPIRY_MINUTES + 1
        )


def test_validate_capture_amount_rejects_excess():
    with pytest.raises(InvalidAmountError):
        TransactionService._validate_capture_amount(
            Decimal("10"),
            Decimal("5")
        )
