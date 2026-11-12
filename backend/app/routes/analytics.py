from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.services.analytics import calculate_analytics
from app.services.categorization_store import get_categorizations
from app.services.paypal_transactions import PayPalTransactionsError, paypal_transactions_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
async def analytics_summary(start_date: datetime, end_date: datetime) -> dict:
    """Summarize actual PayPal Sandbox transactions for an RFC 3339 range."""
    if start_date.tzinfo is None or start_date.utcoffset() is None:
        raise HTTPException(status_code=422, detail="start_date must include a timezone, such as Z.")
    if end_date.tzinfo is None or end_date.utcoffset() is None:
        raise HTTPException(status_code=422, detail="end_date must include a timezone, such as Z.")
    start_utc = start_date.astimezone(timezone.utc)
    end_utc = end_date.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise HTTPException(status_code=422, detail="end_date must be after start_date.")

    try:
        paypal_data = await paypal_transactions_service.search_transactions(
            start_utc, end_utc, fetch_all=True
        )
    except PayPalTransactionsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    transactions = paypal_data["transactions"]
    transaction_ids = {
        str(transaction["transaction_id"])
        for transaction in transactions
        if transaction.get("transaction_id")
    }
    summary = calculate_analytics(transactions, get_categorizations(transaction_ids))
    return {
        "environment": "sandbox",
        "start_date": paypal_data["start_date"],
        "end_date": paypal_data["end_date"],
        **summary,
    }
