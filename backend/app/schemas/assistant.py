from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class AssistantChatResponse(BaseModel):
    conversation_id: str
    answer: str
    explanation: str | None = None
    environment: Literal["sandbox"] = "sandbox"
    start_date: datetime
    end_date: datetime
    date_range_label: str
    data_available: bool
    ai_status: Literal["used", "unavailable", "not_needed"]
    warning: str | None = None
    facts: dict[str, Any]
