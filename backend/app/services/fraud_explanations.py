"""Groq generates prose for rule findings only; risk scores remain rule-based."""

import json
import logging
import os

import httpx

from app.services.groq_categorization import GROQ_API_URL, GROQ_MODEL

logger = logging.getLogger(__name__)

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["transaction_id", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["explanations"],
    "additionalProperties": False,
}


class FraudExplanationError(Exception):
    pass


async def explain_fraud_findings(findings: list[dict[str, object]]) -> dict[str, str]:
    if not findings:
        return {}
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise FraudExplanationError("Groq is not configured for fraud explanations.")

    request_body = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "max_completion_tokens": 1600,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Explain each supplied, deterministic risk-rule finding in plain language. "
                    "Use only the supplied transaction IDs, risk labels, and reasons. Do not infer "
                    "fraud, intent, identity, or new facts. Do not change risk levels, scores, or "
                    "recommended actions. Return one short explanation for each input ID."
                ),
            },
            {"role": "user", "content": json.dumps({"findings": findings})},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "fraud_rule_explanations",
                "strict": True,
                "schema": EXPLANATION_SCHEMA,
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
        logger.warning("Groq fraud explanation transport failure exception_type=%s", type(exc).__name__)
        raise FraudExplanationError("Groq could not be reached for fraud explanations.") from exc

    if response.status_code == 401 or response.status_code == 403:
        logger.warning("Groq fraud explanation authentication failed http_status=%s", response.status_code)
        raise FraudExplanationError("Groq rejected the configured API key.")
    if response.status_code == 429:
        logger.warning("Groq fraud explanation rate limited http_status=429")
        raise FraudExplanationError("Groq rate-limited fraud explanations.")
    if response.is_error:
        logger.warning("Groq fraud explanation request failed http_status=%s", response.status_code)
        raise FraudExplanationError("Groq could not explain the detected rule patterns.")

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        payload = json.loads(content)
        entries = payload["explanations"]
        if not isinstance(entries, list):
            raise ValueError("Expected explanations array")
        expected_ids = {str(item["transaction_id"]) for item in findings}
        results = {}
        for item in entries:
            transaction_id = item.get("transaction_id")
            explanation = item.get("explanation")
            if transaction_id in expected_ids and isinstance(explanation, str) and explanation.strip():
                results[transaction_id] = explanation.strip()[:500]
        return results
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        logger.warning("Groq fraud explanation response was malformed exception_type=%s", type(exc).__name__)
        raise FraudExplanationError("Groq returned an invalid fraud explanation response.") from exc
