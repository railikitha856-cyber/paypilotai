from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

TransactionCategory = Literal[
    "Sales",
    "Refunds",
    "Subscriptions",
    "Software",
    "Supplies",
    "Payroll",
    "Payment Fees",
    "Services",
    "Other",
]
TransactionDirection = Literal["income", "refund", "expense", "unknown"]


class CategorizeTransactionRequest(BaseModel):
    transaction_id: str | None = Field(default=None, min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    transaction_type: str = Field(default="unknown", max_length=100)


class CategorizeTransactionResponse(BaseModel):
    category: TransactionCategory
    confidence: int = Field(ge=0, le=100)
    explanation: str = Field(min_length=1, max_length=240)
    direction: TransactionDirection
