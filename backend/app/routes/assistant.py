import asyncio
from datetime import datetime, timezone
import logging
import uuid

from fastapi import APIRouter, HTTPException, Response

from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse
from app.services.assistant import (
    CONVERSATION_TTL,
    MAX_CONVERSATIONS,
    build_deterministic_answer,
    classify_question,
    format_date_range,
    parse_date_range,
    retrieve_financial_facts,
)
from app.services.groq_assistant import AssistantAIError, explain_question
from app.services.paypal_transactions import PayPalTransactionsError

router = APIRouter(prefix="/api/assistant", tags=["AI assistant"])
logger = logging.getLogger(__name__)

# Process-local conversational context. Only the previous topic and selected
# dates are retained; raw messages, financial rows, and credentials are not cached.
_sessions: dict[str, dict] = {}
_sessions_lock = asyncio.Lock()


async def _get_session(conversation_id: str | None) -> tuple[str, dict]:
    now = datetime.now(timezone.utc)
    async with _sessions_lock:
        expired = [key for key, value in _sessions.items() if now - value["updated_at"] > CONVERSATION_TTL]
        for key in expired:
            _sessions.pop(key, None)
        identifier = conversation_id or str(uuid.uuid4())
        session = _sessions.get(identifier)
        if session is None:
            session = {"topic": None, "date_range": None, "updated_at": now}
            _sessions[identifier] = session
        session["updated_at"] = now
        while len(_sessions) > MAX_CONVERSATIONS:
            oldest = min(_sessions, key=lambda key: _sessions[key]["updated_at"])
            _sessions.pop(oldest, None)
        # Copy values so concurrent requests do not mutate context during awaits.
        return identifier, {"topic": session["topic"], "date_range": session["date_range"]}


async def _save_turn(identifier: str, topic: str, date_range: tuple[datetime, datetime]) -> None:
    async with _sessions_lock:
        session = _sessions.setdefault(identifier, {"topic": None, "date_range": None})
        session["topic"] = topic
        session["date_range"] = date_range
        session["updated_at"] = datetime.now(timezone.utc)


def _groq_fallback(error_code: str) -> tuple[str, str]:
    """Keep the deterministic answer and make the AI fallback visible."""
    warning = f"Groq explanation unavailable ({error_code}); the answer below contains deterministic PayPal Sandbox figures."
    explanation = "These figures were calculated by PayPilot from the actual PayPal Sandbox records for this date range."
    return warning, explanation


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(request: AssistantChatRequest) -> AssistantChatResponse:
    if request.conversation_id:
        try:
            uuid.UUID(request.conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="conversation_id must be a UUID returned by this endpoint.") from exc

    conversation_id, context = await _get_session(request.conversation_id)
    try:
        start_date, end_date = parse_date_range(request.message, previous_range=context["date_range"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        analytics, fraud, customers = await retrieve_financial_facts(start_date, end_date)
    except PayPalTransactionsError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        logger.warning("Assistant data retrieval failure exception_type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail={"code": "assistant_data_unavailable", "message": "PayPal Sandbox financial data could not be prepared."}) from exc

    intent = classify_question(request.message)
    data_available = analytics.get("total_transaction_count", 0) > 0
    facts = {
        "intent": intent,
        "date_range_utc": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "data_available": data_available,
        "total_transaction_count": analytics.get("total_transaction_count", 0),
        "currencies": analytics.get("currencies", []),
        "fraud_assessments": fraud,
    }
    if intent == "customers":
        facts["customer_summary"] = customers
    answer = build_deterministic_answer(intent, analytics, fraud, start_date, end_date, customers)
    explanation = None
    warning = None
    ai_status = "not_needed"
    if data_available:
        try:
            explanation = await explain_question(intent, facts, context["topic"])
            ai_status = "used"
        except AssistantAIError as exc:
            # PayPal facts are already calculated and remain useful if Groq fails.
            # Expose only the normalized error code; never return/log provider bodies.
            logger.warning("Assistant explanation unavailable error_code=%s", exc.code)
            ai_status = "unavailable"
            warning, explanation = _groq_fallback(exc.code)

    await _save_turn(conversation_id, intent, (start_date, end_date))
    return AssistantChatResponse(
        conversation_id=conversation_id,
        answer=answer,
        explanation=explanation,
        start_date=start_date,
        end_date=end_date,
        date_range_label=format_date_range(start_date, end_date),
        data_available=data_available,
        ai_status=ai_status,
        warning=warning,
        facts=facts,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def clear_conversation(conversation_id: str) -> Response:
    try:
        identifier = str(uuid.UUID(conversation_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="conversation_id must be a UUID returned by this endpoint.") from exc
    async with _sessions_lock:
        _sessions.pop(identifier, None)
    return Response(status_code=204)
