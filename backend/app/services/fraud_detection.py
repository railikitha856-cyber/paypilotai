"""Deterministic, explainable transaction risk rules.

Thresholds can be tuned with FRAUD_* environment variables. Historical amount
and frequency comparisons use only the preceding history passed by the caller.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
from typing import Any

ANALYSIS_VERSION = "rules-v2"
REFUND_CODES = {"T1107", "T1115", "T1120"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_decimal(name: str, default: str, minimum: Decimal = Decimal("0.01")) -> Decimal:
    try:
        return max(minimum, Decimal(os.getenv(name, default)))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


@dataclass(frozen=True)
class FraudRuleConfig:
    amount_min_history: int = 5
    high_amount_multiple: Decimal = Decimal("3")
    velocity_window_minutes: int = 10
    velocity_count: int = 5
    denied_window_minutes: int = 60
    denied_count: int = 3
    refund_window_days: int = 7
    refund_count: int = 3
    refund_baseline_days: int = 30
    refund_min_sales: int = 5
    refund_ratio: Decimal = Decimal("0.35")
    frequency_window_hours: int = 24
    frequency_min_count: int = 5
    frequency_multiple: Decimal = Decimal("3")
    frequency_min_history: int = 5
    novel_code_min_history: int = 5

    @classmethod
    def from_environment(cls) -> "FraudRuleConfig":
        return cls(
            amount_min_history=_env_int("FRAUD_AMOUNT_MIN_HISTORY", 5),
            high_amount_multiple=_env_decimal("FRAUD_HIGH_AMOUNT_MULTIPLE", "3"),
            velocity_window_minutes=_env_int("FRAUD_VELOCITY_WINDOW_MINUTES", 10),
            velocity_count=_env_int("FRAUD_VELOCITY_COUNT", 5),
            denied_window_minutes=_env_int("FRAUD_DENIED_WINDOW_MINUTES", 60),
            denied_count=_env_int("FRAUD_DENIED_COUNT", 3),
            refund_window_days=_env_int("FRAUD_REFUND_WINDOW_DAYS", 7),
            refund_count=_env_int("FRAUD_REFUND_COUNT", 3),
            refund_baseline_days=_env_int("FRAUD_REFUND_BASELINE_DAYS", 30),
            refund_min_sales=_env_int("FRAUD_REFUND_MIN_SALES", 5),
            refund_ratio=_env_decimal("FRAUD_REFUND_RATIO", "0.35"),
            frequency_window_hours=_env_int("FRAUD_FREQUENCY_WINDOW_HOURS", 24),
            frequency_min_count=_env_int("FRAUD_FREQUENCY_MIN_COUNT", 5),
            frequency_multiple=_env_decimal("FRAUD_FREQUENCY_MULTIPLE", "3"),
            frequency_min_history=_env_int("FRAUD_FREQUENCY_MIN_HISTORY", 5),
            novel_code_min_history=_env_int("FRAUD_NOVEL_CODE_MIN_HISTORY", 5),
        )


DEFAULT_CONFIG = FraudRuleConfig.from_environment()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _is_refund(transaction: dict[str, Any], explicit_refund_originals: set[str] | None = None) -> bool:
    if str(transaction.get("type") or "").upper() in REFUND_CODES:
        return True
    return (
        str(transaction.get("status") or "").upper() == "V"
        and str(transaction.get("transaction_id") or "") not in (explicit_refund_originals or set())
    )


def _is_successful_incoming(transaction: dict[str, Any]) -> bool:
    status = str(transaction.get("status") or "").upper()
    event_code = str(transaction.get("type") or "").upper()
    amount = _amount(transaction.get("amount"))
    try:
        amount_is_positive = Decimal(str(transaction.get("amount"))) > 0
    except (InvalidOperation, TypeError, ValueError):
        amount_is_positive = False
    return (
        status == "S" and not _is_refund(transaction)
        and not event_code.startswith(("T01", "T11"))
        and amount is not None and amount > 0 and amount_is_positive
    )


def _risk_level(score: int) -> str:
    if score <= 30:
        return "Low"
    if score <= 70:
        return "Medium"
    return "High"


def _rule_result(
    transaction: dict[str, Any], history: list[dict[str, Any]],
    all_transactions: list[dict[str, Any]], config: FraudRuleConfig,
) -> dict[str, Any]:
    transaction_id = transaction.get("transaction_id")
    amount = _amount(transaction.get("amount"))
    currency = str(transaction.get("currency") or "").upper()
    status = str(transaction.get("status") or "").upper()
    event_code = str(transaction.get("type") or "").upper()
    tx_time = _parse_datetime(transaction.get("transaction_date"))
    explicit_refund_originals = {
        str(row.get("reference_id")) for row in all_transactions
        if row.get("reference_id") and str(row.get("type") or "").upper() in REFUND_CODES
    }

    missing = []
    if not transaction_id: missing.append("transaction ID")
    if amount is None: missing.append("amount")
    if not currency: missing.append("currency")
    if not status: missing.append("status")
    if tx_time is None: missing.append("transaction date")
    reasons: list[str] = []
    weights: list[int] = []

    if missing:
        reasons.append("Assessment is limited because required transaction information is missing: " + ", ".join(missing) + ".")
        score, recommendation = 0, "Review"
        explanation = "There is not enough transaction information for a complete automated assessment. Review the available record manually."
    else:
        # Amount baseline is prior successful incoming activity in the same currency.
        prior_sales = [
            row for row in history
            if str(row.get("currency") or "").upper() == currency
            and _is_successful_incoming(row) and _parse_datetime(row.get("transaction_date")) is not None
            and _parse_datetime(row.get("transaction_date")) < tx_time
        ]
        peer_amounts = [value for row in prior_sales if (value := _amount(row.get("amount"))) is not None]
        if len(peer_amounts) >= config.amount_min_history:
            ordered = sorted(peer_amounts)
            median = ordered[len(ordered) // 2]
            threshold = median * config.high_amount_multiple
            if amount > threshold:
                reasons.append(
                    f"Unusually high amount: {currency} {amount} is above {config.high_amount_multiple}x the historical median ({currency} {threshold.quantize(Decimal('0.01'))}), based on {len(peer_amounts)} same-currency successful incoming transactions."
                )
                weights.append(35)
        else:
            reasons.append(
                f"Insufficient history for amount comparison: {len(peer_amounts)} prior same-currency successful incoming transactions; {config.amount_min_history} are required. Amount alone does not increase this risk score."
            )

        timed = [(row, _parse_datetime(row.get("transaction_date"))) for row in all_transactions]
        timed = [(row, stamp) for row, stamp in timed if stamp is not None]
        velocity_window = timedelta(minutes=config.velocity_window_minutes)
        rapid = [(row, stamp) for row, stamp in timed if abs((stamp - tx_time).total_seconds()) <= velocity_window.total_seconds()]
        if len(rapid) >= config.velocity_count:
            reasons.append(f"Rapid activity: {len(rapid)} transactions occurred within {config.velocity_window_minutes} minutes of this transaction (threshold: {config.velocity_count}).")
            weights.append(35)

        denied_window = timedelta(minutes=config.denied_window_minutes)
        denied = [row for row, stamp in timed if str(row.get("status") or "").upper() == "D" and timedelta(0) <= tx_time - stamp <= denied_window]
        if status == "D" and len(denied) >= config.denied_count:
            reasons.append(f"Repeated denied attempts: {len(denied)} denied transactions within {config.denied_window_minutes} minutes (threshold: {config.denied_count}).")
            weights.append(40)

        if _is_refund(transaction, explicit_refund_originals):
            refunds_week = [row for row, stamp in timed if _is_refund(row, explicit_refund_originals)
                            and str(row.get("currency") or "").upper() == currency
                            and timedelta(0) <= tx_time - stamp <= timedelta(days=config.refund_window_days)]
            sales = [row for row, stamp in timed if _is_successful_incoming(row)
                     and str(row.get("currency") or "").upper() == currency
                     and timedelta(0) <= tx_time - stamp <= timedelta(days=config.refund_baseline_days)]
            refund_rows = [row for row, stamp in timed if _is_refund(row, explicit_refund_originals)
                           and str(row.get("currency") or "").upper() == currency
                           and timedelta(0) <= tx_time - stamp <= timedelta(days=config.refund_baseline_days)]
            sales_total = sum((_amount(row.get("amount")) or Decimal("0") for row in sales), Decimal("0"))
            refund_total = sum((_amount(row.get("amount")) or Decimal("0") for row in refund_rows), Decimal("0"))
            ratio = refund_total / sales_total if sales_total else Decimal("0")
            frequent = len(refunds_week) >= config.refund_count
            high_ratio = len(sales) >= config.refund_min_sales and ratio >= config.refund_ratio
            if frequent or high_ratio:
                reasons.append(
                    f"Unusual refund pattern: {len(refunds_week)} refunds in {config.refund_window_days} days (threshold: {config.refund_count}) and 30-day refunded value is {ratio:.0%} of successful incoming value (ratio threshold: {config.refund_ratio:.0%}, minimum sales: {config.refund_min_sales})."
                )
                weights.append(35)

        historical_valid = [row for row in history if _parse_datetime(row.get("transaction_date")) is not None
                            and _parse_datetime(row.get("transaction_date")) < tx_time]
        if len(historical_valid) >= config.frequency_min_history:
            current_window = [(row, stamp) for row, stamp in timed
                              if timedelta(0) <= tx_time - stamp <= timedelta(hours=config.frequency_window_hours)]
            baseline_days = 30
            daily_average = Decimal(len(historical_valid)) / Decimal(baseline_days)
            threshold = max(Decimal(config.frequency_min_count), daily_average * config.frequency_multiple)
            if Decimal(len(current_window)) > threshold:
                reasons.append(
                    f"Sudden frequency increase: {len(current_window)} transactions in {config.frequency_window_hours} hours; historical daily average {daily_average:.1f}, spike threshold {threshold:.1f} (based on {len(historical_valid)} prior transactions)."
                )
                weights.append(35)
        else:
            reasons.append(
                f"Insufficient history for frequency comparison: {len(historical_valid)} prior transactions; {config.frequency_min_history} are required."
            )

        historical_currency = [row for row in history if str(row.get("currency") or "").upper() == currency]
        historical_codes = {str(row.get("type") or "").upper() for row in historical_currency if row.get("type")}
        if len(historical_currency) >= config.novel_code_min_history and event_code and event_code not in historical_codes and status == "S":
            reasons.append(f"Unusual transaction behaviour: event code {event_code} has not appeared in the 30-day historical comparison.")
            weights.append(10)

        score = max(0, min(100, sum(weights)))
        level = _risk_level(score)
        recommendation = {"Low": "Approve", "Medium": "Review", "High": "Escalate"}[level]
        findings = [reason for reason in reasons if not reason.startswith("Insufficient history")]
        has_history_limitations = any(reason.startswith("Insufficient history") for reason in reasons)
        if score == 0 and has_history_limitations:
            recommendation = "Review"
        explanation = (
            "No risk patterns were triggered. " + " ".join(reason for reason in reasons if reason.startswith("Insufficient history"))
            if not findings else "Rule-based findings: " + " ".join(findings + [reason for reason in reasons if reason.startswith("Insufficient history")])
        )

    score = max(0, min(100, int(score)))
    return {
        "transaction_id": transaction_id,
        "transaction_date": transaction.get("transaction_date"),
        "amount": str(transaction.get("amount")) if transaction.get("amount") is not None else None,
        "currency": currency or None,
        "risk_score": score,
        "risk_level": _risk_level(score),
        "reasons": reasons,
        "explanation": explanation,
        "recommended_action": recommendation,
        "review_status": "unreviewed",
        "analysis_version": ANALYSIS_VERSION,
    }


def analyze_transactions(
    transactions: list[dict[str, Any]],
    historical_transactions: list[dict[str, Any]] | None = None,
    config: FraudRuleConfig | None = None,
) -> list[dict[str, Any]]:
    """Score each current transaction; payer fields are never inspected."""
    history = historical_transactions or []
    all_transactions = history + transactions
    selected_config = config or DEFAULT_CONFIG
    return [_rule_result(tx, history, all_transactions, selected_config) for tx in transactions]
