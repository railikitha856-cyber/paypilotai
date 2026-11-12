"""PayPilot chat orchestration, date interpretation, and deterministic replies."""

import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.services.analytics import calculate_analytics, is_successful_incoming
from app.services.categorization_store import get_categorizations, get_fraud_analyses
from app.services.fraud_detection import ANALYSIS_VERSION
from app.services.paypal_transactions import paypal_transactions_service

MAX_CONVERSATIONS = 250
CONVERSATION_TTL = timedelta(hours=2)
_MONTH_PATTERN = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(\d{4}))?\b", re.I)
_ISO_DATES_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def classify_question(message: str) -> str:
    text = message.casefold()
    if any(word in text for word in ("customer", "customers", "buyer", "buyers", "client", "clients")):
        return "customers"
    if any(word in text for word in ("fraud", "suspicious", "risk", "flagged", "need attention", "attention")):
        return "fraud"
    if any(word in text for word in ("refund", "refunded", "chargeback")):
        return "refunds"
    if any(word in text for word in ("fee", "fees", "paypal charge")):
        return "fees"
    if any(word in text for word in ("expense", "expenses", "spending", "spent", "spend")):
        return "expenses"
    if any(word in text for word in ("category", "categories", "categorized")):
        return "categories"
    if any(word in text for word in ("average", "avg", "mean transaction")):
        return "average"
    if any(word in text for word in ("transaction count", "number of transactions", "how many transaction", "transaction volume", "count of transaction")):
        return "transaction_count"
    if any(word in text for word in ("daily", "per day", "each day", "day by day")):
        return "daily_trend"
    if any(word in text for word in ("monthly", "per month", "each month", "month by month")):
        return "monthly_trend"
    if any(word in text for word in ("revenue", "sales", "income", "earned")):
        return "revenue"
    return "performance"


def _aware_range(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end, time.max.replace(microsecond=0), tzinfo=timezone.utc),
    )


def _has_period(text: str) -> bool:
    return bool(
        _ISO_DATES_PATTERN.search(text)
        or _MONTH_PATTERN.search(text)
        or re.search(r"\b(today|yesterday|this month|month to date|last month|this year|year to date|last\s+\d+\s+days?)\b", text, re.I)
    )


def parse_date_range(
    message: str,
    *,
    now: datetime | None = None,
    previous_range: tuple[datetime, datetime] | None = None,
) -> tuple[datetime, datetime]:
    """Parse common conversational periods; inherit the previous period on follow-ups."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    text = message.casefold()
    iso_dates = _ISO_DATES_PATTERN.findall(message)
    if len(iso_dates) >= 2:
        first, last = date.fromisoformat(iso_dates[0]), date.fromisoformat(iso_dates[1])
        if last < first:
            raise ValueError("The end date must be on or after the start date.")
        return _aware_range(first, last)
    if len(iso_dates) == 1:
        selected = date.fromisoformat(iso_dates[0])
        return _aware_range(selected, selected)

    month_match = _MONTH_PATTERN.search(message)
    if month_match:
        month_name, year_text = month_match.groups()
        month_number = list(calendar.month_name).index(month_name.capitalize())
        year = int(year_text) if year_text else current.year - (1 if month_number > current.month else 0)
        month_start = date(year, month_number, 1)
        month_end = date(year, month_number, calendar.monthrange(year, month_number)[1])
        return _aware_range(month_start, month_end)

    today = current.date()
    if "yesterday" in text:
        yesterday = today - timedelta(days=1)
        return _aware_range(yesterday, yesterday)
    if "today" in text:
        return _aware_range(today, today)
    if "last month" in text:
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        return _aware_range(last_month_end.replace(day=1), last_month_end)
    if "this month" in text or "month to date" in text:
        return (datetime.combine(today.replace(day=1), time.min, tzinfo=timezone.utc), current)
    if "this year" in text or "year to date" in text:
        return (datetime(today.year, 1, 1, tzinfo=timezone.utc), current)
    days_match = re.search(r"\blast\s+(\d+)\s+days?\b", text)
    if days_match:
        days = int(days_match.group(1))
        if not 1 <= days <= 3650:
            raise ValueError("Choose a period between 1 and 3650 days.")
        return current - timedelta(days=days), current

    if previous_range and not _has_period(text):
        return previous_range
    return current - timedelta(days=30), current


def summarize_fraud_assessments(
    start_date: datetime, end_date: datetime, assessments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for item in assessments if assessments is not None else get_fraud_analyses(limit=None, analysis_version=ANALYSIS_VERSION):
        raw_date = item.get("transaction_date")
        try:
            stamp = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if start_date <= stamp <= end_date:
            selected.append(item)
    counts = {level: sum(item.get("risk_level") == level for item in selected) for level in ("High", "Medium", "Low")}
    return {"assessments_found": len(selected), "risk_level_counts": counts, "status": "risk assessments, not confirmed fraud"}


def format_date_range(start_date: datetime, end_date: datetime) -> str:
    """Format an inclusive UTC range consistently for answer text and UI metadata."""
    if start_date.tzinfo is None or start_date.utcoffset() is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None or end_date.utcoffset() is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    start_utc = start_date.astimezone(timezone.utc)
    end_utc = end_date.astimezone(timezone.utc)
    first, last = start_utc.date(), end_utc.date()
    if first == last:
        return f"{first.strftime('%B')} {first.day}, {first.year}"
    if first.year == last.year and first.month == last.month:
        return f"{first.strftime('%B')} {first.day}–{last.day}, {first.year}"
    if first.year == last.year:
        return f"{first.strftime('%B')} {first.day}–{last.strftime('%B')} {last.day}, {first.year}"
    return f"{first.strftime('%B')} {first.day}, {first.year}–{last.strftime('%B')} {last.day}, {last.year}"


def _format_currency_lines(currencies: list[dict[str, Any]], field: str, label: str) -> list[str]:
    if not currencies:
        return []
    return [f"{label} — {row['currency']}: {row[field]}" for row in currencies]


def _decimal_sort_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def build_deterministic_answer(
    intent: str,
    analytics: dict[str, Any],
    fraud: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
    customer_summary: dict[str, Any] | None = None,
) -> str:
    """Render figures from the existing analytics calculation; never ask the LLM to total them."""
    currencies = analytics.get("currencies", [])
    total_count = analytics.get("total_transaction_count", 0)
    period = format_date_range(start_date, end_date)
    if intent == "customers":
        summary = customer_summary or {"customer_count": None, "records_missing_identifier": 0, "successful_incoming_count": 0}
        successful_count = summary.get("successful_incoming_count", 0)
        if successful_count == 0:
            return f"PayPal Sandbox data for {period}. Unique customers: 0; no successful incoming payments were returned for this period."
        if summary.get("customer_count") is None:
            missing = summary.get("records_missing_identifier", 0)
            return (f"PayPal Sandbox data for {period}. The customer count cannot be determined because PayPal did not provide "
                    f"a customer account identifier for {missing} of {successful_count} successful incoming payment records. "
                    "Transaction count is not used as a substitute for customer count.")
        return f"PayPal Sandbox data for {period}. Unique customers: {summary['customer_count']} based on successful incoming payments with PayPal customer account identifiers."
    if total_count == 0:
        return f"No PayPal Sandbox transactions were returned for {period}. There are no actual transaction figures to summarize for this period."

    lines = [f"PayPal Sandbox data for {period}."]
    if intent == "revenue":
        for row in currencies:
            count = row.get("successful_incoming_count", 0)
            phrase = "successful incoming transaction" if count == 1 else "successful incoming transactions"
            lines.append(f"Successful incoming revenue — {row['currency']}: {row['total_revenue']}, generated from {count} {phrase}.")
    elif intent == "refunds":
        for row in currencies:
            count = row.get("refund_transaction_count", 0)
            phrase = "refund transaction" if count == 1 else "refund transactions"
            lines.append(f"Refunds — {row['currency']}: {row['total_refunds']} across {count} {phrase}.")
    elif intent == "fees":
        for row in currencies:
            count = row.get("fee_transaction_count", 0)
            phrase = "fee-bearing transaction" if count == 1 else "fee-bearing transactions"
            lines.append(f"PayPal fees — {row['currency']}: {row['total_paypal_fees']} across {count} {phrase}.")
    elif intent == "average":
        lines.extend(_format_currency_lines(currencies, "average_transaction_value", "Average successful incoming transaction"))
    elif intent == "transaction_count":
        lines.append(f"Total transactions across all currencies: {total_count}.")
        lines.extend(f"{row['currency']}: {row['transaction_count']} transactions." for row in currencies)
    elif intent == "daily_trend":
        for row in currencies:
            points = row.get("daily_revenue", [])
            lines.append(f"{row['currency']} daily revenue: " + (", ".join(f"{point['date']} {point['revenue']}" for point in points[-14:]) if points else "no successful incoming revenue records."))
            if len(points) > 14:
                lines.append(f"Showing the latest 14 of {len(points)} daily points for {row['currency']}.")
    elif intent == "monthly_trend":
        for row in currencies:
            points = row.get("monthly_revenue", [])
            lines.append(f"{row['currency']} monthly revenue: " + (", ".join(f"{point['month']} {point['revenue']}" for point in points) if points else "no successful incoming revenue records."))
    elif intent == "categories":
        for row in currencies:
            categories = row.get("category_breakdown", [])
            lines.append(f"{row['currency']} categorized transactions: " + (", ".join(f"{item['category']} ({item['transaction_count']}, {item['amount']})" for item in categories) if categories else "no saved AI category results."))
    elif intent == "expenses":
        expense_rows = [(row["currency"], row.get("expense_breakdown", [])) for row in currencies]
        expense_rows = [(currency, rows) for currency, rows in expense_rows if rows]
        if expense_rows:
            for currency, rows in expense_rows:
                top_rows = sorted(rows, key=lambda item: _decimal_sort_value(item["amount"]), reverse=True)[:5]
                lines.append(f"Largest saved AI-categorized outgoing categories for {currency}: " + "; ".join(f"{expense['category']} {expense['amount']} ({expense['transaction_count']} transactions)" for expense in top_rows) + ".")
        else:
            lines.append("No saved AI-categorized outgoing expense transactions are available for this period, so largest expenses cannot be determined from the current data.")
    elif intent == "fraud":
        counts = fraud["risk_level_counts"]
        if fraud["assessments_found"]:
            lines.append(f"Saved transaction risk assessments: {fraud['assessments_found']} total — High {counts['High']}, Medium {counts['Medium']}, Low {counts['Low']}. These are risk assessments, not confirmed fraud.")
        else:
            lines.append("No saved risk assessments were found for this period. This does not mean transactions have been cleared; analyze transactions on the Fraud Detection page to create assessments.")
    else:  # business performance overview
        lines.append(f"Transactions: {total_count} across {len(currencies)} currencies.")
        lines.extend(_format_currency_lines(currencies, "total_revenue", "Successful incoming revenue"))
        lines.extend(_format_currency_lines(currencies, "total_refunds", "Refunds"))
        lines.extend(_format_currency_lines(currencies, "total_paypal_fees", "PayPal fees"))
        lines.extend(_format_currency_lines(currencies, "average_transaction_value", "Average successful incoming transaction"))
        counts = fraud["risk_level_counts"]
        lines.append(f"Saved risk assessments: {fraud['assessments_found']} (High {counts['High']}, Medium {counts['Medium']}, Low {counts['Low']}); assessments are not confirmed fraud.")
    return "\n".join(lines)


async def retrieve_financial_facts(start_date: datetime, end_date: datetime) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fetch actual Sandbox rows and delegate all money math to calculate_analytics."""
    result = await paypal_transactions_service.search_transactions(
        start_date, end_date, fetch_all=True, include_customer_identifier=True,
    )
    transactions = result.get("transactions", [])
    transaction_ids = {str(row["transaction_id"]) for row in transactions if row.get("transaction_id")}
    analytics = calculate_analytics(transactions, get_categorizations(transaction_ids))
    fraud = summarize_fraud_assessments(start_date, end_date)
    explicit_refund_originals = {
        str(row.get("reference_id")) for row in transactions
        if row.get("reference_id") and str(row.get("type") or "").upper() in {"T1107", "T1115", "T1120"}
    }
    eligible = [row for row in transactions if is_successful_incoming(row, explicit_refund_originals)]
    identifiers = {str(row["customer_identifier"]) for row in eligible if row.get("customer_identifier")}
    missing = sum(not row.get("customer_identifier") for row in eligible)
    customers = {
        "customer_count": len(identifiers) if missing == 0 else None,
        "successful_incoming_count": len(eligible),
        "records_missing_identifier": missing,
    }
    return analytics, fraud, customers
