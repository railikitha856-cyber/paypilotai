"""PayPal Sandbox Transaction Search API client and response mapping."""

import logging
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.paypal_oauth import (
    PAYPAL_SANDBOX_BASE_URL,
    PayPalAuthError,
    PayPalOAuthService,
    paypal_oauth_service,
)

TRANSACTIONS_URL = f"{PAYPAL_SANDBOX_BASE_URL}/v1/reporting/transactions"
logger = logging.getLogger(__name__)
MAX_PAYPAL_RANGE = timedelta(days=31)
# Keep each report window at 30 elapsed days or less, below PayPal's 31-day cap.
MAX_CHUNK_RANGE = timedelta(days=30)
BOUNDARY_STEP = timedelta(microseconds=1)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_api_datetime(value: datetime) -> str:
    # PayPal Transaction Search expects whole-second RFC 3339 timestamps.
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_provider_error(response: httpx.Response) -> str:
    """Return only PayPal's short error code/issue labels, never raw response data."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    parts = []
    name = payload.get("name")
    if isinstance(name, str):
        parts.append(name[:80])
    details = payload.get("details")
    if isinstance(details, list):
        for detail in details[:3]:
            if isinstance(detail, dict) and isinstance(detail.get("issue"), str):
                parts.append(detail["issue"][:80])
    return f" ({', '.join(parts)})" if parts else ""


def _transaction_diagnostic_rows(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlisted transaction metadata only; never include payer details or IDs."""
    rows = []
    for detail in details:
        info = detail.get("transaction_info") or {}
        amount = info.get("transaction_amount") or {}
        rows.append({
            "transaction_initiation_date": info.get("transaction_initiation_date"),
            "transaction_status": info.get("transaction_status"),
            "transaction_event_code": info.get("transaction_event_code"),
            "transaction_amount": amount.get("value"),
            "currency": amount.get("currency_code"),
        })
    return rows


def _log_search_metadata(
    *, status_code: int, payload: dict[str, Any], details: list[dict[str, Any]],
    start_date: str, end_date: str, page: int,
) -> None:
    metadata = {
        "http_status": status_code,
        "start_date": start_date,
        "end_date": end_date,
        "page": page,
        "total_items": payload.get("total_items"),
        "total_pages": payload.get("total_pages"),
        "transaction_details_returned": len(details),
        "transactions": _transaction_diagnostic_rows(details),
    }
    if os.getenv("PAYPAL_TRANSACTION_DIAGNOSTICS", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("PayPal Transaction Search sanitized metadata %s", json.dumps(metadata, separators=(",", ":")))


def split_date_range(start_date: datetime, end_date: datetime) -> list[tuple[datetime, datetime]]:
    """Split ranges over PayPal's limit into contiguous, non-overlapping 30-day windows."""
    if end_date <= start_date:
        raise ValueError("end_date must be after start_date")
    if end_date - start_date <= MAX_PAYPAL_RANGE:
        return [(start_date, end_date)]

    chunks: list[tuple[datetime, datetime]] = []
    cursor = start_date
    while cursor < end_date:
        chunk_end = min(cursor + MAX_CHUNK_RANGE, end_date)
        chunks.append((cursor, chunk_end))
        if chunk_end >= end_date:
            break
        cursor = chunk_end + BOUNDARY_STEP
    return chunks


class PayPalTransactionsError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _map_transaction(detail: dict[str, Any], *, include_customer_identifier: bool = False) -> dict[str, Any]:
    info = detail.get("transaction_info") or {}
    amount = info.get("transaction_amount") or {}
    payer_info = detail.get("payer_info") or {}
    payer_name = payer_info.get("payer_name") or {}

    # Only return minimal payer contact details when PayPal actually provides them.
    payer = None
    payer_fields = {
        key: value
        for key, value in {
            "name": " ".join(
                part for part in (payer_name.get("given_name"), payer_name.get("surname")) if part
            ) or None,
            "email": payer_info.get("email_address"),
        }.items()
        if value
    }
    if payer_fields:
        payer = payer_fields

    mapped = {
        "transaction_id": info.get("transaction_id"),
        "transaction_date": info.get("transaction_initiation_date"),
        "amount": amount.get("value"),
        "currency": amount.get("currency_code"),
        "status": info.get("transaction_status"),
        "type": info.get("transaction_event_code"),
        "description": info.get("transaction_subject"),
        "fee_amount": (info.get("fee_amount") or {}).get("value"),
        "fee_currency": (info.get("fee_amount") or {}).get("currency_code"),
        "reference_id": info.get("paypal_reference_id"),
        "payer": payer,
    }
    # Keep the account identifier private to assistant aggregation. The normal
    # transaction API never returns it to callers or the React app.
    if include_customer_identifier:
        mapped["customer_identifier"] = payer_info.get("account_id")
    return mapped


class PayPalTransactionsService:
    def __init__(self, oauth_service: PayPalOAuthService) -> None:
        self._oauth_service = oauth_service

    async def search_transactions(
        self, start_date: datetime, end_date: datetime, *, fetch_all: bool = False,
        include_customer_identifier: bool = False,
    ) -> dict[str, Any]:
        try:
            chunks = split_date_range(start_date, end_date)
        except ValueError as exc:
            raise PayPalTransactionsError(
                "paypal_invalid_date_range", "end_date must be after start_date.", 400
            ) from exc

        if len(chunks) == 1:
            result = await self._search_single_range(
                *chunks[0], fetch_all=fetch_all,
                include_customer_identifier=include_customer_identifier,
            )
            # Keep the caller's full requested range in response metadata even if
            # PayPal normalizes the values it echoes back.
            result["start_date"] = _format_datetime(start_date)
            result["end_date"] = _format_datetime(end_date)
            return result

        all_transactions: list[dict[str, Any]] = []
        seen_transaction_ids: set[str] = set()
        total_pages = 0
        for chunk_start, chunk_end in chunks:
            try:
                # Every chunk is fully paginated so the aggregate never represents
                # only the first page of one part of the requested range.
                result = await self._search_single_range(
                    chunk_start, chunk_end, fetch_all=True,
                    include_customer_identifier=include_customer_identifier,
                )
            except PayPalTransactionsError as exc:
                chunk_label = f"{_format_api_datetime(chunk_start)} to {_format_api_datetime(chunk_end)}"
                raise PayPalTransactionsError(
                    exc.code,
                    f"PayPal Transaction Search failed for date range {chunk_label}: {exc.message}",
                    exc.status_code,
                ) from exc

            total_pages += int(result.get("total_pages") or 0)
            for transaction in result.get("transactions") or []:
                transaction_id = transaction.get("transaction_id")
                if transaction_id:
                    transaction_id = str(transaction_id)
                    if transaction_id in seen_transaction_ids:
                        continue
                    seen_transaction_ids.add(transaction_id)
                all_transactions.append(transaction)

        return {
            "environment": "sandbox",
            "start_date": _format_datetime(start_date),
            "end_date": _format_datetime(end_date),
            "total_items": len(all_transactions),
            "page": 1,
            "page_size": 500,
            "total_pages": total_pages,
            "transactions": all_transactions,
        }

    async def _search_single_range(
        self, start_date: datetime, end_date: datetime, *, fetch_all: bool = False,
        include_customer_identifier: bool = False,
    ) -> dict[str, Any]:
        params = {
            "start_date": _format_api_datetime(start_date),
            "end_date": _format_api_datetime(end_date),
            "fields": "transaction_info,payer_info" if include_customer_identifier else "transaction_info",
            "page": 1,
            "page_size": 500,
        }

        try:
            token = await self._oauth_service.get_access_token()
        except PayPalAuthError as exc:
            raise PayPalTransactionsError(
                "paypal_authentication_failed",
                "PayPal Sandbox authentication failed. Check the backend Sandbox credentials.",
                503,
            ) from exc

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        TRANSACTIONS_URL,
                        params=params,
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    )
            except httpx.HTTPError as exc:
                raise PayPalTransactionsError(
                    "paypal_unavailable", "PayPal Sandbox could not be reached. Try again shortly.", 502
                ) from exc

            if response.status_code == 401 and attempt == 0:
                await self._oauth_service.invalidate_access_token(token)
                try:
                    token = await self._oauth_service.get_access_token()
                except PayPalAuthError as exc:
                    raise PayPalTransactionsError(
                        "paypal_authentication_failed",
                        "PayPal Sandbox authentication failed. Check the backend Sandbox credentials.",
                        503,
                    ) from exc
                continue

            if response.status_code == 401:
                await self._oauth_service.invalidate_access_token(token)
                raise PayPalTransactionsError(
                    "paypal_authentication_failed",
                    "PayPal Sandbox rejected the refreshed access token. Check the app credentials.",
                    502,
                )

            if response.status_code == 403:
                raise PayPalTransactionsError(
                    "transaction_search_permission_missing",
                    "PayPal Sandbox denied Transaction Search. Enable the Transaction search feature for this app, save it, and retry after requesting a fresh token.",
                    403,
                )

            if response.status_code == 429:
                raise PayPalTransactionsError(
                    "paypal_rate_limited", "PayPal Sandbox rate-limited this request. Try again shortly.", 503
                )

            if response.is_error:
                logger.warning(
                    "PayPal Transaction Search returned HTTP %s for range %s to %s, page 1",
                    response.status_code,
                    params["start_date"],
                    params["end_date"],
                )
                if response.status_code == 400:
                    raise PayPalTransactionsError(
                        "paypal_invalid_date_range",
                        "PayPal Sandbox rejected the date range. Check the selected dates and try again."
                        + _safe_provider_error(response),
                        400,
                    )
                raise PayPalTransactionsError(
                    "paypal_api_error", "PayPal Sandbox could not complete the transaction search.", 502
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise PayPalTransactionsError(
                    "paypal_invalid_response", "PayPal Sandbox returned an invalid response.", 502
                ) from exc

            details = payload.get("transaction_details") or []
            total_pages = payload.get("total_pages", 1 if details else 0)
            _log_search_metadata(
                status_code=response.status_code, payload=payload, details=details,
                start_date=params["start_date"], end_date=params["end_date"], page=1,
            )
            if fetch_all and total_pages > 1:
                for page in range(2, total_pages + 1):
                    params["page"] = page
                    try:
                        async with httpx.AsyncClient(timeout=20.0) as client:
                            page_response = await client.get(
                                TRANSACTIONS_URL,
                                params=params,
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                            )
                    except httpx.HTTPError as exc:
                        raise PayPalTransactionsError(
                            "paypal_unavailable", "PayPal Sandbox could not be reached. Try again shortly.", 502
                        ) from exc
                    if page_response.status_code == 401:
                        await self._oauth_service.invalidate_access_token(token)
                        raise PayPalTransactionsError(
                            "paypal_authentication_failed",
                            "PayPal Sandbox access expired during transaction retrieval. Refresh and retry.",
                            502,
                        )
                    if page_response.status_code == 403:
                        raise PayPalTransactionsError(
                            "transaction_search_permission_missing",
                            "PayPal Sandbox denied Transaction Search. Enable the Transaction search feature for this app, save it, and retry after requesting a fresh token.",
                            403,
                        )
                    if page_response.status_code == 429:
                        raise PayPalTransactionsError(
                            "paypal_rate_limited", "PayPal Sandbox rate-limited this request. Try again shortly.", 503
                        )
                    if page_response.is_error:
                        logger.warning(
                            "PayPal Transaction Search returned HTTP %s for range %s to %s, page %s",
                            page_response.status_code,
                            params["start_date"],
                            params["end_date"],
                            page,
                        )
                        if page_response.status_code == 400:
                            raise PayPalTransactionsError(
                                "paypal_invalid_date_range",
                                "PayPal Sandbox rejected the date range. Check the selected dates and try again."
                                + _safe_provider_error(page_response),
                                400,
                            )
                        raise PayPalTransactionsError(
                            "paypal_api_error", "PayPal Sandbox could not complete the transaction search.", 502
                        )
                    try:
                        page_payload = page_response.json()
                    except ValueError as exc:
                        raise PayPalTransactionsError(
                            "paypal_invalid_response", "PayPal Sandbox returned an invalid response.", 502
                        ) from exc
                    page_details = page_payload.get("transaction_details") or []
                    _log_search_metadata(
                        status_code=page_response.status_code, payload=page_payload, details=page_details,
                        start_date=params["start_date"], end_date=params["end_date"], page=page,
                    )
                    details.extend(page_details)
            mapped_transactions = [
                _map_transaction(item, include_customer_identifier=include_customer_identifier)
                for item in details
            ]
            if os.getenv("PAYPAL_TRANSACTION_DIAGNOSTICS", "").strip().lower() in {"1", "true", "yes"}:
                logger.info(
                    "PayPal Transaction Search parsed result start_date=%s end_date=%s pages=%s raw_count=%s mapped_count=%s",
                    params["start_date"], params["end_date"], total_pages, len(details), len(mapped_transactions),
                )
            return {
                "environment": "sandbox",
                "start_date": payload.get("start_date") or params["start_date"],
                "end_date": payload.get("end_date") or params["end_date"],
                "total_items": payload.get("total_items", len(details)),
                "page": payload.get("page", 1),
                "page_size": params["page_size"],
                "total_pages": total_pages,
                "transactions": mapped_transactions,
            }

        raise PayPalTransactionsError(
            "paypal_authentication_failed", "PayPal Sandbox authentication failed. Try again.", 502
        )


paypal_transactions_service = PayPalTransactionsService(paypal_oauth_service)
