"""TEST DATA only: isolated synthetic scenarios for the fraud rule engine.

This suite calls the deterministic analyzer directly. It does not contact
PayPal/Groq and does not persist transactions or assessments.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.services.fraud_detection import analyze_transactions


TEST_NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def test_transaction(name, stamp, amount="20.00", status="S", event="T0000"):
    """Create a clearly marked in-memory TEST DATA record."""
    return {
        "transaction_id": f"TEST-DATA-{name}",
        "transaction_date": stamp.isoformat().replace("+00:00", "Z"),
        "amount": amount,
        "currency": "USD",
        "status": status,
        "type": event,
        "test_data": True,
    }


def assert_assessment(test_case, result, score, level, explanation_text):
    test_case.assertEqual(result["risk_score"], score)
    test_case.assertEqual(result["risk_level"], level)
    test_case.assertIn(explanation_text, result["explanation"])
    test_case.assertTrue(result["transaction_id"].startswith("TEST-DATA-"))


class SyntheticFraudScenarioTests(unittest.TestCase):
    def test_normal_payment_with_adequate_history_is_low_risk(self):
        history = [
            test_transaction(f"normal-history-{i}", TEST_NOW - timedelta(days=i + 1))
            for i in range(5)
        ]
        target = test_transaction("normal-target", TEST_NOW)

        result = analyze_transactions([target], history)[0]

        assert_assessment(self, result, 0, "Low", "No risk patterns were triggered")
        self.assertEqual(result["recommended_action"], "Approve")

    def test_large_amount_is_compared_to_historical_median(self):
        history = [
            test_transaction(f"amount-history-{i}", TEST_NOW - timedelta(days=i + 1), "100.00")
            for i in range(5)
        ]
        target = test_transaction("large-target", TEST_NOW, "500.00")

        result = analyze_transactions([target], history)[0]

        assert_assessment(self, result, 35, "Medium", "Unusually high amount")
        self.assertTrue(any("3x the historical median" in reason for reason in result["reasons"]))

    def test_short_time_payment_cluster_is_explained(self):
        burst = [
            test_transaction(f"velocity-{i}", TEST_NOW - timedelta(minutes=4 - i))
            for i in range(5)
        ]

        result = analyze_transactions([burst[-1]], burst[:-1])[0]

        assert_assessment(self, result, 35, "Medium", "Rapid activity")
        self.assertTrue(any("within 10 minutes" in reason for reason in result["reasons"]))

    def test_repeated_denials_are_explained(self):
        denied = [
            test_transaction(f"denied-{i}", TEST_NOW - timedelta(minutes=2 - i), status="D")
            for i in range(3)
        ]

        result = analyze_transactions([denied[-1]], denied[:-1])[0]

        assert_assessment(self, result, 40, "Medium", "Repeated denied attempts")
        self.assertTrue(any("within 60 minutes" in reason for reason in result["reasons"]))

    def test_repeated_refunds_are_explained(self):
        sales = [
            test_transaction(f"refund-sales-{i}", TEST_NOW - timedelta(days=i + 1), "100.00")
            for i in range(5)
        ]
        refunds = [
            test_transaction(f"refund-{i}", TEST_NOW - timedelta(hours=2 - i), "-40.00", event="T1107")
            for i in range(3)
        ]

        result = analyze_transactions([refunds[-1]], sales + refunds[:-1])[0]

        assert_assessment(self, result, 35, "Medium", "Unusual refund pattern")
        self.assertTrue(any("3 refunds in 7 days" in reason for reason in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
