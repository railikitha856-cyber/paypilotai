"""Groq phrasing for PayPilot's deterministic financial facts."""

import json
import logging
import os
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.services.groq_categorization import GROQ_API_URL, GROQ_MODEL

logger = logging.getLogger(__name__)
UNSUPPORTED_FIGURE_PATTERN = re.compile(
    r"[\d$€£₹¥]|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|hundred|thousand|million|billion|percent|percentage|"
    r"dollars?|rupees?|euros?|pounds?|yen|usd|inr|eur|gbp|jpy)\b",
    re.I,
)
SAFE_ERROR_TYPES = {
    "invalid_request_error", "authentication_error", "permission_error",
    "rate_limit_error", "server_error", "api_error",
}
SAFE_ERROR_CODES = {
    "model_not_found", "model_decommissioned", "model_disabled", "invalid_api_key",
    "json_validate_failed", "rate_limit_exceeded", "insufficient_quota",
    "request_too_large", "context_length_exceeded", "service_unavailable",
    "invalid_request_error",
}
SAFE_ERROR_PARAMS = {"model", "messages", "response_format", "temperature", "max_completion_tokens", "max_tokens"}


class AssistantAIError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _safe_provider_error(response: httpx.Response, api_key: str = "") -> tuple[str, str, str, str]:
    """Extract sanitized provider diagnostics; never log credentials or user content."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    raw_type = error.get("type")
    raw_code = error.get("code")
    error_type = raw_type if isinstance(raw_type, str) and raw_type in SAFE_ERROR_TYPES else "unknown"
    error_code = raw_code if isinstance(raw_code, str) and raw_code in SAFE_ERROR_CODES else "unknown"
    raw_param = error.get("param")
    error_param = raw_param if isinstance(raw_param, str) and raw_param in SAFE_ERROR_PARAMS else "unknown"
    raw_message = error.get("message")
    message = raw_message if isinstance(raw_message, str) else ""
    # Messages can echo request values. Strip secrets, addresses, dates, amounts,
    # and long opaque strings before they reach logs.
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", message, flags=re.I)
    message = re.sub(r"gsk_[A-Za-z0-9_-]+", "[redacted]", message)
    message = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email]", message)
    message = re.sub(r"\b\d+(?:[.,:/-]\d+)*\b", "[value]", message)
    message = re.sub(r"\s+", " ", message).strip()[:300]
    return error_type, error_code, error_param, message or "no provider message"


def _raise_provider_error(response: httpx.Response, api_key: str = "") -> None:
    error_type, error_code, error_param, error_message = _safe_provider_error(response, api_key)
    logger.warning(
        "Groq assistant API error http_status=%s error_type=%s error_code=%s error_param=%s message=%s",
        response.status_code, error_type, error_code, error_param, error_message,
    )
    if response.status_code in (401, 403) or error_type == "authentication_error" or error_code == "invalid_api_key":
        raise AssistantAIError("groq_authentication_failed", "Groq rejected the configured API key.", 502)
    if response.status_code == 429 or error_code == "rate_limit_exceeded":
        raise AssistantAIError("groq_rate_limited", "Groq rate-limited this request. Try again shortly.", 503)
    if response.status_code == 404 or error_code in {"model_not_found", "model_decommissioned", "model_disabled"}:
        raise AssistantAIError("groq_model_unavailable", "The configured Groq model is unavailable to this API key.", 502)
    if response.status_code == 400 or error_code in {"json_validate_failed", "invalid_request_error"}:
        raise AssistantAIError("groq_invalid_request", "Groq rejected the assistant request or structured-output schema.", 502)
    if response.status_code == 413 or error_code in {"request_too_large", "context_length_exceeded"}:
        raise AssistantAIError("groq_request_too_large", "The assistant context exceeded Groq's request limit.", 502)
    if response.status_code >= 500:
        raise AssistantAIError("groq_unavailable", "Groq is temporarily unavailable. Try again shortly.", 502)
    raise AssistantAIError("groq_request_failed", "The AI Assistant could not prepare an explanation.", 502)


class AssistantExplanation(BaseModel):
    summary: str = Field(min_length=1, max_length=500)


def sanitize_explanation(summary: str) -> str:
    """Reject AI prose that tries to introduce unsupported figures or currencies."""
    if UNSUPPORTED_FIGURE_PATTERN.search(summary):
        logger.warning("Groq assistant response rejected reason=unsupported_numeric_content; deterministic Python answer retained")
        return "The figures below are calculated from the available PayPal Sandbox records for the selected period."
    return summary


def _build_request(intent: str, facts: dict, previous_intent: str | None) -> dict:
    """Build a qualitative-only Groq prompt with no financial or customer values."""
    prompt = {
        "question_topic": intent,
        "previous_question_topic": previous_intent,
        "data_available": bool(facts.get("data_available")),
    }
    return {
        "model": GROQ_MODEL,
        "temperature": 0,
        "max_completion_tokens": 512,
        "reasoning_effort": "low",
        "reasoning_format": "hidden",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are PayPilot AI, a financial operations assistant for a small business. "
                    "Write a brief, qualitative explanation of the requested metric or data limitation. "
                    "Only explain what the topic means and whether data is available. Do not state, calculate, "
                    "estimate, or repeat amounts, counts, percentages, dates, currencies, customer details, "
                    "transaction IDs, or trends. Do not make claims about business performance. "
                    "Do not provide professional financial, tax, legal, or investment advice. "
                    "Return a JSON object with exactly one non-empty string property named summary."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        # This model supports JSON Object Mode; Python validates the response.
        "response_format": {"type": "json_object"},
    }


def _parse_success_response(response: httpx.Response) -> str:
    content = response.json()["choices"][0]["message"]["content"]
    return AssistantExplanation.model_validate(json.loads(content)).summary.strip()


async def explain_question(intent: str, facts: dict, previous_intent: str | None = None) -> str:
    """Ask Groq for a qualitative explanation only; Python owns every figure."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise AssistantAIError(
            "groq_not_configured",
            "Groq is not configured. Add GROQ_API_KEY to backend/.env and restart FastAPI.",
            503,
        )

    body = _build_request(intent, facts, previous_intent)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.warning("Groq assistant transport failure exception_type=%s", type(exc).__name__)
        raise AssistantAIError("groq_unavailable", "Could not reach Groq. Try again shortly.", 502) from exc

    if response.is_error:
        _raise_provider_error(response, api_key)
    try:
        summary = _parse_success_response(response)
    except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        logger.warning("Groq assistant response validation failure exception_type=%s", type(exc).__name__)
        raise AssistantAIError("groq_invalid_response", "Groq returned an invalid assistant response.", 502) from exc

    # Financial numbers are emitted only by deterministic Python formatting.
    return sanitize_explanation(summary)
