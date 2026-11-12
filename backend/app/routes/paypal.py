from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.services.paypal_oauth import (
    TRANSACTION_SEARCH_SCOPE,
    PayPalAuthError,
    paypal_oauth_service,
)
from app.services.paypal_transactions import (
    PayPalTransactionsError,
    paypal_transactions_service,
)

router = APIRouter(prefix="/api/paypal", tags=["paypal"])


@router.get("/status")
async def paypal_status() -> dict[str, str]:
    """Report whether Sandbox OAuth succeeds without exposing secrets or tokens."""
    try:
        await paypal_oauth_service.get_access_token()
    except PayPalAuthError:
        return {"status": "disconnected"}
    return {"status": "connected"}


@router.get("/debug-scope")
async def paypal_debug_scope() -> dict[str, bool]:
    """Inspect only whether the cached PayPal token has Transaction Search scope."""
    return {
        "transaction_search_scope": paypal_oauth_service.has_cached_scope(
            TRANSACTION_SEARCH_SCOPE
        )
    }


@router.get("/transactions")
async def paypal_transactions(start_date: datetime, end_date: datetime) -> dict:
    """Search actual Sandbox transactions for an RFC 3339 date-time range."""
    if start_date.tzinfo is None or start_date.utcoffset() is None:
        raise HTTPException(status_code=422, detail="start_date must include a timezone, such as Z.")
    if end_date.tzinfo is None or end_date.utcoffset() is None:
        raise HTTPException(status_code=422, detail="end_date must include a timezone, such as Z.")

    start_utc = start_date.astimezone(timezone.utc)
    end_utc = end_date.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise HTTPException(status_code=422, detail="end_date must be after start_date.")

    try:
        # The table should show all matching records, including every API page.
        return await paypal_transactions_service.search_transactions(start_utc, end_utc, fetch_all=True)
    except PayPalTransactionsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
