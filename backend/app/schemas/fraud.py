from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FraudAnalyzeRequest(BaseModel):
    start_date: datetime | None = None
    end_date: datetime | None = None
    transaction_ids: list[str] | None = Field(default=None, max_length=1000)


class FraudReviewUpdate(BaseModel):
    review_status: Literal["approved", "review", "escalated"]
