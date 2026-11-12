"""Unit tests with synthetic TEST DATA; these records are never persisted or sent to PayPal."""

import unittest
from datetime import datetime, timezone

import httpx
from app.main import app
from app.routes.assistant import _groq_fallback
from app.services.assistant import build_deterministic_answer, classify_question, format_date_range, parse_date_range, summarize_fraud_assessments
from app.services.paypal_transactions import _map_transaction
from app.services.groq_assistant import (
    AssistantAIError,
    _build_request,
    _parse_success_response,
    _raise_provider_error,
    sanitize_explanation,
)


TEST_START = datetime(2026, 11, 1, tzinfo=timezone.utc)
TEST_END = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
TEST_ANALYTICS = {
    "total_transaction_count": 3,
    "currencies": [
        {"currency": "USD", "total_revenue": "100.00", "total_refunds": "20.00", "total_paypal_fees": "3.00", "average_transaction_value": "50.00", "transaction_count": 2, "successful_incoming_count": 2, "refund_transaction_count": 1, "fee_transaction_count": 2,
         "daily_revenue": [{"date": "2026-11-10", "revenue": "100.00"}], "monthly_revenue": [{"month": "2026-11", "revenue": "100.00"}],
         "category_breakdown": [{"category": "Sales", "transaction_count": 2, "amount": "100.00"}], "expense_breakdown": []},
        {"currency": "INR", "total_revenue": "500.00", "total_refunds": "0.00", "total_paypal_fees": "10.00", "average_transaction_value": "500.00", "transaction_count": 1, "successful_incoming_count": 1, "refund_transaction_count": 0, "fee_transaction_count": 1,
         "daily_revenue": [], "monthly_revenue": [], "category_breakdown": [], "expense_breakdown": []},
    ],
}
TEST_FRAUD = {"assessments_found": 2, "risk_level_counts": {"High": 1, "Medium": 1, "Low": 0}, "status": "risk assessments, not confirmed fraud"}


class AssistantAnswerTests(unittest.TestCase):
    def test_chat_endpoint_is_registered(self):
        paths = set(app.openapi()["paths"])
        self.assertIn("/api/assistant/chat", paths)
        self.assertIn("/api/assistant/conversations/{conversation_id}", paths)

    def test_ai_numeric_claim_is_discarded(self):
        explanation = sanitize_explanation("Revenue increased by 20%.")
        self.assertIn("calculated from the available PayPal Sandbox records", explanation)

    def test_groq_success_response_is_parsed_and_validated(self):
        response = httpx.Response(200, json={"choices": [{"message": {"content": '{"summary":"Revenue is income from successful payments."}'}}]})
        self.assertEqual(_parse_success_response(response), "Revenue is income from successful payments.")

    def test_groq_failure_keeps_deterministic_figures_and_clear_fallback(self):
        answer = build_deterministic_answer("revenue", TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        warning, explanation = _groq_fallback("groq_unavailable")
        self.assertIn("USD: 100.00", answer)
        self.assertIn("INR: 500.00", answer)
        self.assertIn("generated from 2 successful incoming transactions", answer)
        self.assertIn("generated from 1 successful incoming transaction.", answer)
        self.assertIn("Groq explanation unavailable", warning)
        self.assertIn("deterministic PayPal Sandbox figures", warning)
        self.assertIn("actual PayPal Sandbox records", explanation)

    def test_groq_request_uses_supported_json_mode_without_financial_or_personal_facts(self):
        request = _build_request("revenue", {
            "data_available": True,
            "date_range_utc": {"start": "2026-02-01", "end": "2026-02-28"},
            "currencies": [{"currency": "USD", "total_revenue": "123.45"}],
            "customer_summary": {"customer_count": 3},
            "transactions": [{"transaction_id": "private-id", "payer": {"email": "private@example.com"}}],
        }, None)
        self.assertEqual(request["response_format"], {"type": "json_object"})
        prompt_text = request["messages"][1]["content"]
        for private_value in ("2026-02-01", "123.45", "private-id", "private@example.com"):
            self.assertNotIn(private_value, prompt_text)

    def test_provider_errors_are_distinguished_without_returning_provider_body(self):
        cases = [
            (400, {"error": {"type": "invalid_request_error", "code": "json_validate_failed", "message": "private provider details"}}, "groq_invalid_request"),
            (404, {"error": {"code": "model_not_found", "message": "private provider details"}}, "groq_model_unavailable"),
            (429, {"error": {"code": "rate_limit_exceeded", "message": "private provider details"}}, "groq_rate_limited"),
            (503, {"error": {"code": "service_unavailable", "message": "private provider details"}}, "groq_unavailable"),
        ]
        for status, body, expected_code in cases:
            response = httpx.Response(status, json=body)
            with self.subTest(status=status), self.assertRaises(AssistantAIError) as caught:
                _raise_provider_error(response)
            self.assertEqual(caught.exception.code, expected_code)
            self.assertNotIn("private provider details", caught.exception.message)

    def test_provider_diagnostic_logs_redact_secrets_personal_data_and_amounts(self):
        response = httpx.Response(400, json={"error": {"type": "invalid_request_error", "code": "invalid_request_error", "param": "response_format", "message": "bad payload with gsk_supersecret for person@example.com amount 5000"}})
        with self.assertLogs("app.services.groq_assistant", level="WARNING") as captured:
            with self.assertRaises(AssistantAIError):
                _raise_provider_error(response)
        log_text = " ".join(captured.output)
        self.assertIn("http_status=400", log_text)
        self.assertIn("error_param=response_format", log_text)
        for forbidden in ("gsk_supersecret", "person@example.com", "5000"):
            self.assertNotIn(forbidden, log_text)

    def test_revenue_question_uses_calculated_figures(self):
        intent = classify_question("How much revenue did I generate this month?")
        answer = build_deterministic_answer(intent, TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertEqual(intent, "revenue")
        self.assertIn("USD: 100.00", answer)
        self.assertIn("INR: 500.00", answer)

    def test_customer_question_and_unique_id_count(self):
        question = "How many customers did I have in November?"
        intent = classify_question(question)
        start, end = parse_date_range(question, now=TEST_END)
        self.assertEqual(intent, "customers")
        self.assertEqual((start.date().isoformat(), end.date().isoformat()), ("2026-11-01", "2026-11-30"))
        answer = build_deterministic_answer(intent, TEST_ANALYTICS, TEST_FRAUD, start, end, {
            "customer_count": 2, "successful_incoming_count": 3, "records_missing_identifier": 0,
        })
        self.assertIn("Unique customers: 2", answer)
        self.assertNotIn("Total transactions", answer)

    def test_customer_count_unknown_if_identifier_missing(self):
        answer = build_deterministic_answer("customers", TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END, {
            "customer_count": None, "successful_incoming_count": 3, "records_missing_identifier": 1,
        })
        self.assertIn("customer count cannot be determined", answer)
        self.assertIn("not used as a substitute", answer)

    def test_customer_identifier_is_private_mapping_only(self):
        detail = {"transaction_info": {"transaction_id": "private-tx"}, "payer_info": {"account_id": "private-account"}}
        self.assertNotIn("customer_identifier", _map_transaction(detail))
        self.assertEqual(_map_transaction(detail, include_customer_identifier=True)["customer_identifier"], "private-account")

    def test_refund_question(self):
        question = "What is my refund total?"
        answer = build_deterministic_answer(classify_question(question), TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertIn("Refunds — USD: 20.00 across", answer)
        self.assertIn("Refunds — INR: 0.00 across", answer)
        self.assertIn("USD: 20.00 across 1 refund transaction", answer)

    def test_paypal_fee_question(self):
        question = "How much did I pay in PayPal fees?"
        answer = build_deterministic_answer(classify_question(question), TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertEqual(classify_question(question), "fees")
        self.assertIn("PayPal fees — USD: 3.00 across", answer)
        self.assertIn("PayPal fees — INR: 10.00 across", answer)
        self.assertIn("USD: 3.00 across 2 fee-bearing transactions", answer)

    def test_transaction_count_question(self):
        answer = build_deterministic_answer(classify_question("What is my transaction count?"), TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertIn("Total transactions across all currencies: 3", answer)
        self.assertIn("USD: 2 transactions", answer)

    def test_fraud_question_uses_assessment_language(self):
        answer = build_deterministic_answer(classify_question("Which transactions need attention?"), TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertIn("High 1, Medium 1, Low 0", answer)
        self.assertIn("not confirmed fraud", answer)

    def test_explicit_date_range_is_included_in_answer(self):
        start, end = parse_date_range("Revenue from 2026-11-01 to 2026-11-08", now=TEST_END)
        answer = build_deterministic_answer("revenue", TEST_ANALYTICS, TEST_FRAUD, start, end)
        self.assertEqual(start.date().isoformat(), "2026-11-01")
        self.assertEqual(end.date().isoformat(), "2026-11-08")
        self.assertIn("November 1–8, 2026", answer)

    def test_monthly_date_label_is_consistent_and_inclusive_in_utc(self):
        start = datetime.fromisoformat("2026-11-01T05:30:00+05:30")
        end = datetime.fromisoformat("2026-12-01T05:29:59+05:30")
        expected = "November 1–30, 2026"
        self.assertEqual(format_date_range(start, end), expected)
        answer = build_deterministic_answer("revenue", TEST_ANALYTICS, TEST_FRAUD, start, end)
        self.assertIn(expected, answer)

    def test_month_without_year_uses_most_recent_full_month(self):
        cases = {
            "What was my revenue in February?": ("2026-02-01", "2026-02-28"),
            "How much did I earn in October?": ("2026-10-01", "2026-10-31"),
            "Show revenue for November.": ("2026-11-01", "2026-11-30"),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                start, end = parse_date_range(question, now=TEST_END)
                self.assertEqual((start.date().isoformat(), end.date().isoformat()), expected)

    def test_explicit_month_year_is_respected(self):
        start, end = parse_date_range("Revenue in February 2024", now=TEST_END)
        self.assertEqual((start.date().isoformat(), end.date().isoformat()), ("2024-02-01", "2024-02-29"))

    def test_multi_currency_results_stay_separate(self):
        answer = build_deterministic_answer("revenue", TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertIn("USD: 100.00", answer)
        self.assertIn("INR: 500.00", answer)
        self.assertNotIn("600.00", answer)

    def test_empty_data_is_explicit(self):
        answer = build_deterministic_answer("revenue", {"total_transaction_count": 0, "currencies": []}, {"assessments_found": 0, "risk_level_counts": {"High": 0, "Medium": 0, "Low": 0}}, TEST_START, TEST_END)
        self.assertIn("No PayPal Sandbox transactions were returned", answer)
        self.assertIn("no actual transaction figures", answer)

    def test_follow_up_inherits_previous_period(self):
        start, end = parse_date_range("How much revenue last month?", now=TEST_END)
        followup_start, followup_end = parse_date_range("And what about refunds?", now=TEST_END, previous_range=(start, end))
        self.assertEqual((followup_start, followup_end), (start, end))

    def test_expense_question_discloses_missing_categorization(self):
        answer = build_deterministic_answer("expenses", TEST_ANALYTICS, TEST_FRAUD, TEST_START, TEST_END)
        self.assertIn("No saved AI-categorized outgoing expense transactions", answer)

    def test_saved_risk_assessments_are_filtered_by_date(self):
        rows = [
            {"transaction_date": "2026-11-05T12:00:00Z", "risk_level": "High"},
            {"transaction_date": "2026-10-05T12:00:00Z", "risk_level": "Low"},
        ]
        result = summarize_fraud_assessments(TEST_START, TEST_END, rows)
        self.assertEqual(result["assessments_found"], 1)
        self.assertEqual(result["risk_level_counts"]["High"], 1)


if __name__ == "__main__":
    unittest.main()
