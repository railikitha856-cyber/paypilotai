import unittest
from app.main import app
from app.services.analytics import calculate_analytics


class AnalyticsCalculationTests(unittest.TestCase):
    def test_refunds_pending_denied_and_currency_isolation(self):
        transactions = [
            {"transaction_id": "sale-usd", "transaction_date": "2026-11-01T10:00:00Z", "amount": "100.00", "currency": "USD", "status": "S", "type": "T0000", "fee_amount": "3.50", "fee_currency": "USD"},
            {"transaction_id": "refund-usd", "transaction_date": "2026-11-02T10:00:00Z", "amount": "-20.00", "currency": "USD", "status": "S", "type": "T1107"},
            {"transaction_id": "reversal-usd", "transaction_date": "2026-11-02T11:00:00Z", "amount": "15.00", "currency": "USD", "status": "S", "type": "T1100"},
            {"transaction_id": "pending-usd", "transaction_date": "2026-11-03T10:00:00Z", "amount": "900.00", "currency": "USD", "status": "P", "type": "T0000", "fee_amount": "9.00"},
            {"transaction_id": "denied-inr", "transaction_date": "2026-11-03T10:00:00Z", "amount": "8000.00", "currency": "INR", "status": "D", "type": "T0000"},
            {"transaction_id": "sale-inr", "transaction_date": "2026-11-04T10:00:00Z", "amount": "500.00", "currency": "INR", "status": "S", "type": "T0000", "fee_amount": "15.00", "fee_currency": "INR"},
        ]

        result = calculate_analytics(transactions, {"sale-usd": {"category": "Sales"}})
        by_currency = {row["currency"]: row for row in result["currencies"]}

        self.assertEqual(result["total_transaction_count"], 6)
        self.assertEqual(by_currency["USD"]["total_revenue"], "100.00")
        self.assertEqual(by_currency["USD"]["total_refunds"], "20.00")
        self.assertEqual(by_currency["USD"]["total_paypal_fees"], "3.50")
        self.assertEqual(by_currency["USD"]["average_transaction_value"], "100.00")
        self.assertEqual(by_currency["USD"]["transaction_count"], 4)
        self.assertEqual(by_currency["USD"]["successful_incoming_count"], 1)
        self.assertEqual(by_currency["USD"]["refund_transaction_count"], 1)
        self.assertEqual(by_currency["USD"]["fee_transaction_count"], 1)
        self.assertEqual(by_currency["INR"]["total_revenue"], "500.00")
        self.assertEqual(by_currency["INR"]["total_refunds"], "0.00")
        self.assertEqual(by_currency["INR"]["total_paypal_fees"], "15.00")
        self.assertEqual(by_currency["INR"]["average_transaction_value"], "500.00")
        self.assertEqual(by_currency["INR"]["fee_transaction_count"], 1)
        self.assertEqual(by_currency["USD"]["category_breakdown"][0]["category"], "Sales")

    def test_empty_dataset_returns_zero_count_and_no_currency_groups(self):
        self.assertEqual(calculate_analytics([]), {"total_transaction_count": 0, "currencies": []})

    def test_fully_reversed_transaction_counts_as_refund_not_revenue(self):
        result = calculate_analytics([
            {"transaction_id": "reversed", "transaction_date": "2026-11-01T10:00:00Z", "amount": "45.00", "currency": "USD", "status": "V", "type": "T0000"}
        ])
        usd = result["currencies"][0]
        self.assertEqual(usd["total_revenue"], "0.00")
        self.assertEqual(usd["total_refunds"], "45.00")
        self.assertEqual(usd["successful_incoming_count"], 0)

    def test_refund_record_prevents_double_count_of_reversed_original(self):
        result = calculate_analytics([
            {"transaction_id": "original", "transaction_date": "2026-11-01T10:00:00Z", "amount": "45.00", "currency": "USD", "status": "V", "type": "T0000"},
            {"transaction_id": "refund", "reference_id": "original", "transaction_date": "2026-11-02T10:00:00Z", "amount": "-45.00", "currency": "USD", "status": "S", "type": "T1107"},
        ])
        self.assertEqual(result["currencies"][0]["total_refunds"], "45.00")

    def test_expense_breakdown_uses_saved_ai_direction_without_mixing_currencies(self):
        transactions = [
            {"transaction_id": "software", "transaction_date": "2026-11-01T10:00:00Z", "amount": "-29.00", "currency": "USD", "status": "S", "type": "T0000"},
            {"transaction_id": "supply", "transaction_date": "2026-11-02T10:00:00Z", "amount": "-15.00", "currency": "USD", "status": "S", "type": "T0000"},
        ]
        categories = {
            "software": {"category": "Software", "direction": "expense"},
            "supply": {"category": "Supplies", "direction": "expense"},
        }
        result = calculate_analytics(transactions, categories)
        self.assertEqual(result["currencies"][0]["expense_breakdown"][0], {
            "category": "Software", "transaction_count": 1, "amount": "29.00",
        })


class AnalyticsEndpointTests(unittest.TestCase):
    def test_summary_endpoint_is_registered(self):
        paths = set(app.openapi()["paths"])
        self.assertIn("/api/analytics/summary", paths)

if __name__ == "__main__":
    unittest.main()
