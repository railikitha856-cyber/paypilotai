"""Groq-backed transaction categorization using schema-constrained JSON output."""

import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from app.schemas.ai import CategorizeTransactionRequest, CategorizeTransactionResponse

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"
logger = logging.getLogger(__name__)
SAFE_PROVIDER_ERROR_TYPES = {
    "invalid_request_error",
    "authentication_error",
    "permission_error",
    "rate_limit_error",
    "server_error",
    "api_error",
}
SAFE_PROVIDER_ERROR_CODES = {
    "model_not_found",
    "model_decommissioned",
    "model_disabled",
    "invalid_api_key",
    "json_validate_failed",
    "rate_limit_exceeded",
    "insufficient_quota",
    "request_too_large",
    "context_length_exceeded",
    "service_unavailable",
    "invalid_request_error",
}

RESULT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "Sales", "Refunds", "Subscriptions", "Software", "Supplies",
                "Payroll", "Payment Fees", "Services", "Other",
            ],
        },
        "confidence": {"type": "integer"},
        "explanation": {"type": "string"},
        "direction": {"type": "string", "enum": ["income", "refund", "expense", "unknown"]},
    },
    "required": ["category", "confidence", "explanation", "direction"],
    "additionalProperties": False,
}


class CategorizationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _provider_error_metadata(response: httpx.Response) -> tuple[str | None, str | None, str]:
    """Extract only safe diagnostic labels; never log the provider body or message."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}

    def safe_label(value: object, allowed: set[str]) -> str | None:
        # Only log fixed provider error labels; never log arbitrary response strings.
        if isinstance(value, str) and value in allowed:
            return value
        return None

    return (
        safe_label(error.get("type"), SAFE_PROVIDER_ERROR_TYPES),
        safe_label(error.get("code"), SAFE_PROVIDER_ERROR_CODES),
        error.get("message", "") if isinstance(error.get("message"), str) else "",
    )


class GroqCategorizationService:
    async def categorize(
        self, transaction: CategorizeTransactionRequest
    ) -> CategorizeTransactionResponse:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise CategorizationError(
                "groq_not_configured",
                "Groq is not configured. Add GROQ_API_KEY to backend/.env and restart FastAPI.",
                503,
            )

        input_details = {
            "description": transaction.description,
            "amount": str(transaction.amount),
            "currency": transaction.currency.upper(),
            "transaction_type": transaction.transaction_type,
        }
        request_body = {
            "model": GROQ_MODEL,
            "temperature": 0,
            "max_completion_tokens": 250,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Categorize the supplied transaction for bookkeeping. Use only the provided fields; "
                        "do not invent merchants, products, people, dates, or other transaction facts. "
                        "Choose the closest category from the allowed list. If evidence is insufficient, "
                        "choose Other and use direction unknown with low confidence. Explain the category "
                        "briefly using only the supplied details. Confidence is an integer from 0 to 100."
                    ),
                },
                {"role": "user", "content": json.dumps(input_details, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "transaction_categorization",
                    "strict": True,
                    "schema": RESULT_JSON_SCHEMA,
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=request_body,
                )
        except httpx.HTTPError as exc:
            logger.warning("Groq transport failure exception_type=%s", type(exc).__name__)
            raise CategorizationError(
                "groq_unavailable", "Could not reach Groq. Try again shortly.", 502
            ) from exc

        provider_type: str | None = None
        provider_code: str | None = None
        provider_message = ""
        if response.is_error:
            provider_type, provider_code, provider_message = _provider_error_metadata(response)
            logger.warning(
                "Groq API error http_status=%s error_type=%s error_code=%s",
                response.status_code,
                provider_type or "unknown",
                provider_code or "unknown",
            )

        if (
            response.status_code in (401, 403)
            or provider_code == "invalid_api_key"
            or provider_type == "authentication_error"
        ):
            raise CategorizationError(
                "groq_authentication_failed",
                "Groq rejected the configured API key. Check GROQ_API_KEY in backend/.env.",
                502,
            )
        if response.status_code == 429:
            raise CategorizationError(
                "groq_rate_limited", "Groq rate-limited this request. Try again shortly.", 503
            )
        if response.status_code in (400, 404):
            _, provider_code, provider_message = _provider_error_metadata(response)
            error_signature = f"{provider_code or ''} {provider_message}".lower()
            if any(term in error_signature for term in ("model_not_found", "model_decommissioned", "model not found", "model is not available", "model unavailable")):
                raise CategorizationError(
                    "groq_model_unavailable",
                    "The configured Groq model is not available for this API key. Check the model ID and model access.",
                    502,
                )
            raise CategorizationError(
                "groq_invalid_request",
                "Groq rejected the request format or structured-output schema. Check the model and JSON schema configuration.",
                502,
            )
        if response.is_error:
            raise CategorizationError(
                "groq_request_failed", "Groq could not categorize this transaction.", 502
            )

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            result = CategorizeTransactionResponse.model_validate(json.loads(content))
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            logger.warning("Groq response validation failure exception_type=%s", type(exc).__name__)
            raise CategorizationError(
                "groq_invalid_response",
                "Groq returned a response that did not match the categorization format.",
                502,
            ) from exc

        return result


groq_categorization_service = GroqCategorizationService()
