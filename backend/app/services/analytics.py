"""Currency-safe PayPal financial analytics calculations."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

REFUND_EVENT_CODES = {"T1107", "T1115", "T1120"}
FEE_REFUND_EVENT_CODES = {"T1108", "T1109"}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value: Decimal, currency: str) -> str:
    zero_decimal = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
    three_decimal = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
    places = 0 if currency in zero_decimal else 3 if currency in three_decimal else 2
    quantum = Decimal("1").scaleb(-places)
    return f"{value.quantize(quantum):.{places}f}"


def _date(value: Any) -> str | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def is_successful_incoming(
    transaction: dict[str, Any], explicit_refund_originals: set[str] | None = None,
) -> bool:
    """Use PayPal status, event code, and amount to identify successful sales."""
    amount = _decimal(transaction.get("amount"))
    status = str(transaction.get("status") or "").upper()
    event_code = str(transaction.get("type") or "").upper()
    transaction_id = str(transaction.get("transaction_id") or "")
    is_refund = event_code in REFUND_EVENT_CODES or (
        status == "V" and transaction_id not in (explicit_refund_originals or set())
    )
    return (
        status == "S" and not is_refund
        and not event_code.startswith(("T01", "T11"))
        and amount > 0
    )


def calculate_analytics(
    transactions: list[dict[str, Any]],
    category_by_transaction_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate transaction search rows without ever combining currencies.

    Only status S can contribute to revenue or fee charges. Refund event codes and
    fully reversed status V rows are tracked separately from incoming revenue.
    """
    category_by_transaction_id = category_by_transaction_id or {}
    explicit_refund_originals = {
        str(transaction.get("reference_id"))
        for transaction in transactions
        if transaction.get("reference_id")
        and str(transaction.get("type") or "").upper() in REFUND_EVENT_CODES
    }
    frame_rows: list[dict[str, Any]] = []
    for transaction in transactions:
        amount = _decimal(transaction.get("amount"))
        currency = str(transaction.get("currency") or "").upper()
        status = str(transaction.get("status") or "").upper()
        event_code = str(transaction.get("type") or "").upper()
        transaction_id = transaction.get("transaction_id")
        tx_date = _date(transaction.get("transaction_date"))
        if not currency:
            continue

        reversed_row_already_refunded = (
            status == "V"
            and str(transaction_id or "") in explicit_refund_originals
        )
        is_refund = event_code in REFUND_EVENT_CODES or (status == "V" and not reversed_row_already_refunded)
        successful = status == "S"
        fee_event = event_code.startswith("T01")
        fee_refund = event_code in FEE_REFUND_EVENT_CODES
        reversal_event = event_code.startswith("T11")
        incoming = is_successful_incoming(transaction, explicit_refund_originals)

        fee_currency = str(transaction.get("fee_currency") or currency).upper()
        fee_amount = _decimal(transaction.get("fee_amount"))
        if fee_event:
            fee_amount = abs(amount)
            fee_currency = currency
        elif fee_refund:
            fee_amount = -abs(amount)
            fee_currency = currency
        elif fee_amount:
            # PayPal's fee field is an expense on the merchant ledger; normalize it
            # as a positive cost even when Sandbox returns a signed value.
            fee_amount = abs(fee_amount)

        frame_rows.append({
            "transaction_id": transaction_id,
            "currency": currency,
            "status": status,
            "event_code": event_code,
            "amount": amount,
            "date": tx_date,
            "income": amount if incoming else Decimal("0"),
            "refund": abs(amount) if is_refund and (successful or status == "V") and event_code not in FEE_REFUND_EVENT_CODES else Decimal("0"),
            "fee_currency": fee_currency,
            "fee": fee_amount if (successful or status == "V") else Decimal("0"),
            "category": (category_by_transaction_id.get(str(transaction_id)) or {}).get("category"),
            "direction": (category_by_transaction_id.get(str(transaction_id)) or {}).get("direction"),
        })

    if not frame_rows:
        return {"total_transaction_count": 0, "currencies": []}

    frame = pd.DataFrame(frame_rows)
    result: list[dict[str, Any]] = []
    currencies = sorted(set(frame["currency"]) | set(frame["fee_currency"]))
    for currency in currencies:
        group = frame[frame["currency"] == currency]
        successful_incomes = group[group["income"].map(lambda value: value > 0)]
        refunds = group[group["refund"].map(lambda value: value > 0)]
        fees_group = frame[frame["fee_currency"] == currency]
        daily: dict[str, Decimal] = defaultdict(Decimal)
        monthly: dict[str, Decimal] = defaultdict(Decimal)
        for _, row in successful_incomes.iterrows():
            if row["date"]:
                daily[row["date"]] += row["income"]
                monthly[row["date"][:7]] += row["income"]

        categories: dict[str, dict[str, Any]] = {}
        expenses: dict[str, dict[str, Any]] = {}
        for _, row in group.iterrows():
            category = row["category"]
            if not category or row["status"] != "S":
                continue
            entry = categories.setdefault(category, {"transaction_count": 0, "amount": Decimal("0")})
            entry["transaction_count"] += 1
            entry["amount"] += abs(row["amount"])
            if row["direction"] == "expense":
                expense = expenses.setdefault(category, {"transaction_count": 0, "amount": Decimal("0")})
                expense["transaction_count"] += 1
                expense["amount"] += abs(row["amount"])

        total_income = sum(successful_incomes["income"], Decimal("0"))
        income_count = len(successful_incomes)
        total_fees = sum(fees_group["fee"], Decimal("0"))
        result.append({
            "currency": currency,
            "total_revenue": _money(total_income, currency),
            "total_refunds": _money(sum(refunds["refund"], Decimal("0")), currency),
            "total_paypal_fees": _money(total_fees, currency),
            "transaction_count": len(group),
            "successful_incoming_count": income_count,
            "refund_transaction_count": int(len(refunds)),
            "fee_transaction_count": int(sum(1 for value in fees_group["fee"] if value > 0)),
            "average_transaction_value": _money(total_income / income_count if income_count else Decimal("0"), currency),
            "daily_revenue": [{"date": key, "revenue": _money(value, currency)} for key, value in sorted(daily.items())],
            "monthly_revenue": [{"month": key, "revenue": _money(value, currency)} for key, value in sorted(monthly.items())],
            "category_breakdown": [
                {"category": key, "transaction_count": val["transaction_count"], "amount": _money(val["amount"], currency)}
                for key, val in sorted(categories.items())
            ],
            "expense_breakdown": [
                {"category": key, "transaction_count": val["transaction_count"], "amount": _money(val["amount"], currency)}
                for key, val in sorted(expenses.items(), key=lambda item: (-item[1]["amount"], item[0]))
            ],
        })
    return {"total_transaction_count": len(frame), "currencies": result}
