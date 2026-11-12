import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.main import app
from app.schemas.fraud import FraudAnalyzeRequest, FraudReviewUpdate
from app.services.fraud_detection import FraudRuleConfig, analyze_transactions
from app.services import categorization_store
from app.routes import fraud as fraud_routes


def tx(transaction_id, stamp, amount="10.00", status="S", event="T0000", currency="USD", **extra):
    return {
        "transaction_id": transaction_id,
        "transaction_date": stamp.isoformat().replace("+00:00", "Z"),
        "amount": amount,
        "currency": currency,
        "status": status,
        "type": event,
        **extra,
    }


class FraudRuleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

    def test_low_risk_baseline_transaction(self):
        result = analyze_transactions([tx("ordinary", self.now)])[0]
        self.assertEqual((result["risk_score"], result["risk_level"]), (0, "Low"))
        self.assertEqual(result["recommended_action"], "Review")
        self.assertTrue(any("Insufficient history for amount comparison" in reason for reason in result["reasons"]))
        self.assertTrue(any("Insufficient history for frequency comparison" in reason for reason in result["reasons"]))

    def test_medium_risk_unusually_high_amount(self):
        history = [tx(f"past-{index}", self.now - timedelta(days=index + 1), "10.00") for index in range(5)]
        result = analyze_transactions([tx("large", self.now, "100.00")], history)[0]
        self.assertEqual(result["risk_score"], 35)
        self.assertEqual(result["risk_level"], "Medium")
        self.assertEqual(result["recommended_action"], "Review")
        self.assertIn("Unusually high amount", result["reasons"][0])

    def test_five_thousand_alone_does_not_raise_risk_with_insufficient_history(self):
        result = analyze_transactions([tx("large-first", self.now, "5000.00")])[0]
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["recommended_action"], "Review")
        self.assertTrue(any("Amount alone does not increase" in reason for reason in result["reasons"]))

    def test_five_thousand_is_compared_with_same_currency_history(self):
        history = [tx(f"past-{index}", self.now - timedelta(days=index + 1), "500.00") for index in range(5)]
        result = analyze_transactions([tx("large", self.now, "5000.00")], history)[0]
        self.assertEqual(result["risk_score"], 35)
        self.assertTrue(any("Unusually high amount" in reason for reason in result["reasons"]))

    def test_large_amount_with_comparable_history_is_not_flagged_by_amount_rule(self):
        history = [tx(f"past-{index}", self.now - timedelta(days=index + 1), "2500.00") for index in range(5)]
        result = analyze_transactions([tx("large", self.now, "5000.00")], history)[0]
        self.assertFalse(any("Unusually high amount" in reason for reason in result["reasons"]))
        self.assertEqual(result["risk_score"], 0)

    def test_history_is_required_for_sudden_frequency_rule(self):
        current = [tx(f"burst-{index}", self.now - timedelta(minutes=5 - index), "10.00") for index in range(6)]
        result = analyze_transactions(current)[-1]
        self.assertFalse(any("Sudden frequency increase" in reason for reason in result["reasons"]))
        self.assertTrue(any("Insufficient history for frequency comparison" in reason for reason in result["reasons"]))

    def test_frequency_spike_uses_prior_history_baseline(self):
        history = [tx(f"past-{index}", self.now - timedelta(days=index + 1), "10.00") for index in range(5)]
        current = [tx(f"burst-{index}", self.now - timedelta(minutes=5 - index), "10.00") for index in range(6)]
        result = analyze_transactions([current[-1]], history + current[:-1])[0]
        self.assertTrue(any("Sudden frequency increase" in reason for reason in result["reasons"]))

    def test_rule_thresholds_can_be_configured(self):
        config = FraudRuleConfig(amount_min_history=2, high_amount_multiple=Decimal("2"))
        history = [tx(f"past-{index}", self.now - timedelta(days=index + 1), "10.00") for index in range(2)]
        result = analyze_transactions([tx("large", self.now, "25.00")], history, config)[0]
        self.assertEqual(result["risk_score"], 35)

    def test_high_risk_rapid_activity_and_repeated_denials(self):
        denied = [tx(f"denied-{index}", self.now - timedelta(minutes=4 - index), "12.00", "D") for index in range(5)]
        result = analyze_transactions(denied)[-1]
        self.assertGreaterEqual(result["risk_score"], 71)
        self.assertEqual(result["risk_level"], "High")
        self.assertEqual(result["recommended_action"], "Escalate")
        self.assertTrue(any("Repeated denied attempts" in reason for reason in result["reasons"]))
        self.assertTrue(any("Rapid activity" in reason for reason in result["reasons"]))

    def test_unusual_refund_pattern_is_explained(self):
        history = [tx(f"sale-{index}", self.now - timedelta(days=index + 1), "10.00") for index in range(5)]
        refunds = [tx(f"refund-{index}", self.now - timedelta(hours=2 - index), "-20.00", "S", "T1107") for index in range(3)]
        result = analyze_transactions([refunds[-1]], history + refunds[:-1])[0]
        self.assertEqual(result["risk_level"], "Medium")
        self.assertTrue(any("Unusual refund pattern" in reason for reason in result["reasons"]))

    def test_missing_fields_are_reported_and_referred_for_manual_review(self):
        result = analyze_transactions([{"transaction_id": "partial"}])[0]
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["risk_level"], "Low")
        self.assertEqual(result["recommended_action"], "Review")
        self.assertIn("missing", result["reasons"][0])

    def test_payer_identity_does_not_change_risk_assessment(self):
        base = tx("same", self.now)
        with_identity = {**base, "payer": {"name": "Sensitive Name", "email": "private@example.test"}}
        self.assertEqual(analyze_transactions([base]), analyze_transactions([with_identity]))


class FraudPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "fraud-test.sqlite3"
        self.patch_db = patch.object(categorization_store, "DATABASE_PATH", self.db_path)
        self.patch_db.start()
        self.result = {
            "transaction_id": "persisted-1",
            "analysis_version": "rules-v2",
            "transaction_date": "2026-11-09T12:00:00Z",
            "amount": "100.00",
            "currency": "USD",
            "risk_score": 35,
            "risk_level": "Medium",
            "reasons": ["Unusually high amount."],
            "explanation": "The amount differs from prior activity.",
            "recommended_action": "Review",
        }

    def tearDown(self):
        self.patch_db.stop()
        self.temp_dir.cleanup()

    def test_upsert_prevents_duplicate_analysis_and_persists(self):
        categorization_store.save_fraud_analyses([self.result])
        categorization_store.save_fraud_analyses([{**self.result, "risk_score": 40}])
        rows = categorization_store.get_fraud_analyses(analysis_version="rules-v2")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["risk_score"], 40)
        self.assertEqual(rows[0]["reasons"], ["Unusually high amount."])

    def test_manual_review_status_update_is_persisted(self):
        categorization_store.save_fraud_analyses([self.result])
        with patch.object(fraud_routes, "update_fraud_review", categorization_store.update_fraud_review):
            response = fraud_routes.update_review_status("persisted-1", FraudReviewUpdate(review_status="escalated"))
        self.assertEqual(response["transaction"]["review_status"], "escalated")
        categorization_store.save_fraud_analyses([self.result])
        stored = categorization_store.get_fraud_analyses(review_status="escalated", analysis_version="rules-v2")
        self.assertEqual(len(stored), 1)

    def test_fraud_routes_are_registered(self):
        paths = set(app.openapi()["paths"])
        self.assertIn("/api/fraud/analyze", paths)
        self.assertIn("/api/fraud/transactions", paths)
        self.assertIn("/api/fraud/transactions/{transaction_id}/review", paths)

    def test_analysis_route_fetches_history_uses_groq_prose_and_persists_unique_rows(self):
        now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        historical = [tx(f"past-{index}", now - timedelta(days=index + 1), "10.00") for index in range(5)]
        target = tx("large-live", now, "100.00")

        class PayPalFixture:
            calls = []

            async def search_transactions(self, start_date, end_date, *, fetch_all=False):
                self.calls.append((start_date, end_date, fetch_all))
                return {"transactions": historical + [target, dict(target)]}

        async def explanation_fixture(findings):
            return {"large-live": "This amount is above the business's recent successful payment range."}

        start = datetime(2026, 11, 9, tzinfo=timezone.utc)
        end = datetime(2026, 11, 9, 23, 59, 59, tzinfo=timezone.utc)
        fixture = PayPalFixture()
        with patch.object(fraud_routes, "paypal_transactions_service", fixture), \
             patch.object(fraud_routes, "explain_fraud_findings", explanation_fixture):
            payload = asyncio.run(fraud_routes.analyze_paypal_transactions(
                FraudAnalyzeRequest(start_date=start, end_date=end)
            ))

        self.assertEqual(payload["transactions_analyzed"], 1)
        self.assertEqual(payload["transactions_stored"], 1)
        self.assertEqual(payload["results"][0]["risk_score"], 35)
        self.assertIn("above the business", payload["results"][0]["explanation"])
        self.assertEqual(fixture.calls[0][0], start - timedelta(days=30))
        stored = categorization_store.get_fraud_analyses(analysis_version="rules-v2")
        self.assertEqual(len(stored), 1)


if __name__ == "__main__":
    unittest.main()
