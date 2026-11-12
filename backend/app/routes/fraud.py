from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.schemas.fraud import FraudAnalyzeRequest, FraudReviewUpdate
from app.services.categorization_store import (
    get_fraud_analyses,
    save_fraud_analyses,
    update_fraud_review,
)
from app.services.fraud_detection import ANALYSIS_VERSION, analyze_transactions
from app.services.fraud_explanations import FraudExplanationError, explain_fraud_findings
from app.services.paypal_transactions import PayPalTransactionsError, paypal_transactions_service

router = APIRouter(prefix="/api/fraud", tags=["fraud detection"])


def _validate_date(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{field} must include a timezone, such as Z.")
    return value.astimezone(timezone.utc)


def _parse_transaction_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.post("/analyze")
async def analyze_paypal_transactions(request: FraudAnalyzeRequest) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if request.start_date is None and request.end_date is None:
        end_date = now
        start_date = end_date - timedelta(days=30)
    elif request.start_date is None or request.end_date is None:
        raise HTTPException(status_code=422, detail="Provide both start_date and end_date, or omit both for the last 30 days.")
    else:
        start_date = _validate_date(request.start_date, "start_date")
        end_date = _validate_date(request.end_date, "end_date")

    if end_date <= start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date.")

    historical_start = start_date - timedelta(days=30)
    try:
        paypal_data = await paypal_transactions_service.search_transactions(
            historical_start, end_date, fetch_all=True
        )
    except PayPalTransactionsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    target_transactions: list[dict] = []
    historical_transactions: list[dict] = []
    requested_ids = set(request.transaction_ids or [])
    for transaction in paypal_data.get("transactions", []):
        stamp = _parse_transaction_date(transaction.get("transaction_date"))
        transaction_id = str(transaction.get("transaction_id") or "")
        if stamp is None:
            # Keep malformed current-period records available for an explicit
            # incomplete-data assessment; no payer fields are used to classify them.
            if not requested_ids or transaction_id in requested_ids:
                target_transactions.append(transaction)
        elif start_date <= stamp <= end_date:
            if not requested_ids or transaction_id in requested_ids:
                target_transactions.append(transaction)
        elif historical_start <= stamp < start_date:
            historical_transactions.append(transaction)

    def deduplicate(rows: list[dict]) -> list[dict]:
        unique_rows: list[dict] = []
        seen_ids: set[str] = set()
        for row in rows:
            transaction_id = str(row.get("transaction_id") or "")
            if transaction_id and transaction_id in seen_ids:
                continue
            if transaction_id:
                seen_ids.add(transaction_id)
            unique_rows.append(row)
        return unique_rows

    target_transactions = deduplicate(target_transactions)
    historical_transactions = deduplicate(historical_transactions)

    results = analyze_transactions(target_transactions, historical_transactions)
    findings = [
        {"transaction_id": item["transaction_id"], "risk_level": item["risk_level"], "reasons": item["reasons"]}
        for item in results
        if item["risk_score"] > 0 and item["transaction_id"]
    ]
    warnings: list[str] = []
    if findings:
        explanations: dict[str, str] = {}
        for offset in range(0, len(findings), 20):
            try:
                explanations.update(await explain_fraud_findings(findings[offset:offset + 20]))
            except FraudExplanationError:
                warnings.append("Groq explanations are unavailable for some findings; deterministic rule explanations are shown instead.")
                break
        if any(str(item["transaction_id"]) not in explanations for item in findings) and not warnings:
            warnings.append("Groq did not return an explanation for every finding; deterministic rule explanations are shown where needed.")
        for result in results:
            if str(result.get("transaction_id")) in explanations:
                limitation_notes = [
                    reason for reason in result.get("reasons", [])
                    if reason.startswith("Insufficient history")
                ]
                result["explanation"] = explanations[str(result["transaction_id"])]
                if limitation_notes:
                    result["explanation"] += " Historical data limitation: " + " ".join(limitation_notes)

    persisted_results = [item for item in results if item.get("transaction_id")]
    save_fraud_analyses(persisted_results)
    return {
        "environment": "sandbox",
        "analysis_version": ANALYSIS_VERSION,
        "start_date": start_date.isoformat().replace("+00:00", "Z"),
        "end_date": end_date.isoformat().replace("+00:00", "Z"),
        "transactions_analyzed": len(target_transactions),
        "transactions_stored": len(persisted_results),
        "results": results,
        "warnings": warnings,
    }


@router.get("/transactions")
def fraud_transactions(
    limit: int = Query(default=100, ge=1, le=500),
    risk_level: str | None = Query(default=None, pattern="^(Low|Medium|High)$"),
    review_status: str | None = Query(default=None, pattern="^(unreviewed|approved|review|escalated)$"),
) -> dict:
    return {
        "environment": "sandbox",
        "analysis_version": ANALYSIS_VERSION,
        "transactions": get_fraud_analyses(
            limit=limit,
            risk_level=risk_level,
            review_status=review_status,
            analysis_version=ANALYSIS_VERSION,
        ),
    }


@router.patch("/transactions/{transaction_id}/review")
def update_review_status(transaction_id: str, update: FraudReviewUpdate) -> dict:
    result = update_fraud_review(transaction_id, update.review_status, ANALYSIS_VERSION)
    if result is None:
        raise HTTPException(status_code=404, detail="No fraud assessment exists for this transaction.")
    return {"transaction": result}
