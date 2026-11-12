import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.routes import paypal as paypal_routes

from app.services.paypal_transactions import (
    PayPalTransactionsError,
    PayPalTransactionsService,
    _format_api_datetime,
    _map_transaction,
    _transaction_diagnostic_rows,
    split_date_range,
)


class OAuthStub:
    async def get_access_token(self):
        return "test-access-token"

    async def invalidate_access_token(self, token):
        return None


class RecordingTransactionsService(PayPalTransactionsService):
    def __init__(self, *, fail_on_call=None, empty=False):
        super().__init__(OAuthStub())
        self.calls = []
        self.fail_on_call = fail_on_call
        self.empty = empty

    async def _search_single_range(self, start_date, end_date, *, fetch_all=False, include_customer_identifier=False):
        self.calls.append((start_date, end_date, fetch_all))
        call_number = len(self.calls)
        if call_number == self.fail_on_call:
            raise PayPalTransactionsError("paypal_api_error", "The provider rejected this chunk.", 502)
        if self.empty:
            return {"total_items": 0, "total_pages": 0, "transactions": []}
        transaction_id = "shared-boundary-id" if call_number in (1, 2) else f"transaction-{call_number}"
        return {
            "total_items": 1,
            "total_pages": 2,
            "transactions": [{"transaction_id": transaction_id, "currency": "USD"}],
        }


class TransactionRangeChunkingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_7_and_30_day_ranges_stay_single_requests(self):
        for days in (7, 30):
            with self.subTest(days=days):
                self.assertEqual(len(split_date_range(self.start, self.start + timedelta(days=days))), 1)

    def test_90_day_range_uses_three_chunks(self):
        chunks = split_date_range(self.start, self.start + timedelta(days=90))
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(end - start <= timedelta(days=30) for start, end in chunks))

    def test_180_day_range_uses_six_contiguous_chunks(self):
        end = self.start + timedelta(days=180)
        chunks = split_date_range(self.start, end)
        self.assertEqual(len(chunks), 6)
        self.assertEqual(chunks[0][0], self.start)
        self.assertEqual(chunks[-1][1], end)
        self.assertTrue(all(right[0] - left[1] == timedelta(microseconds=1) for left, right in zip(chunks, chunks[1:])))
        self.assertTrue(all(end_date - start_date <= timedelta(days=30) for start_date, end_date in chunks))

    def test_api_boundaries_are_serialized_at_whole_second_precision(self):
        value = self.start + timedelta(days=30, microseconds=1)
        self.assertEqual(_format_api_datetime(value), "2026-01-31T00:00:00Z")

    def test_request_boundaries_match_ui_displayed_utc_midnight(self):
        end = datetime(2026, 11, 13, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(_format_api_datetime(end), "2026-11-13T00:00:00Z")

    def test_transaction_mapping_preserves_pending_status(self):
        row = _map_transaction({"transaction_info": {
            "transaction_id": "private-test-id", "transaction_initiation_date": "2026-11-12T18:37:00Z",
            "transaction_status": "P", "transaction_event_code": "T0000",
            "transaction_amount": {"value": "1000.00", "currency_code": "USD"},
        }})
        self.assertEqual(row["status"], "P")
        self.assertEqual(row["amount"], "1000.00")
        self.assertEqual(row["currency"], "USD")

    def test_diagnostics_include_only_allowlisted_transaction_metadata(self):
        rows = _transaction_diagnostic_rows([{
            "transaction_info": {
                "transaction_id": "private-test-id", "transaction_initiation_date": "2026-11-12T18:37:00Z",
                "transaction_status": "P", "transaction_event_code": "T0000",
                "transaction_amount": {"value": "1000.00", "currency_code": "USD"},
            },
            "payer_info": {"email_address": "private@example.com"},
        }])
        self.assertEqual(rows[0]["transaction_status"], "P")
        self.assertEqual(rows[0]["transaction_amount"], "1000.00")
        self.assertNotIn("transaction_id", rows[0])
        self.assertNotIn("payer_info", rows[0])

    def test_transactions_route_normalizes_utc_and_fetches_every_page(self):
        calls = []

        async def fake_search(start_date, end_date, *, fetch_all=False):
            calls.append((start_date, end_date, fetch_all))
            return {"transactions": [], "total_items": 0, "total_pages": 0}

        start = datetime(2026, 10, 14, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        end = datetime(2026, 11, 13, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        with patch.object(paypal_routes.paypal_transactions_service, "search_transactions", fake_search):
            asyncio.run(paypal_routes.paypal_transactions(start, end))

        self.assertEqual(calls[0][0], datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(calls[0][1], datetime(2026, 11, 13, tzinfo=timezone.utc))
        self.assertTrue(calls[0][2])

    def test_chunked_search_paginates_each_chunk_deduplicates_and_preserves_requested_bounds(self):
        end = self.start + timedelta(days=90)
        service = RecordingTransactionsService()
        result = asyncio.run(service.search_transactions(self.start, end, fetch_all=True))

        self.assertEqual(len(service.calls), 3)
        self.assertTrue(all(fetch_all for _, _, fetch_all in service.calls))
        self.assertEqual(service.calls[0][0], self.start)
        self.assertEqual(service.calls[-1][1], end)
        self.assertEqual(result["start_date"], "2026-01-01T00:00:00Z")
        self.assertEqual(result["end_date"], "2026-04-01T00:00:00Z")
        self.assertEqual(result["total_items"], 2)
        self.assertEqual(result["total_pages"], 6)

    def test_empty_results_across_chunks_return_empty_set(self):
        service = RecordingTransactionsService(empty=True)
        result = asyncio.run(service.search_transactions(self.start, self.start + timedelta(days=90), fetch_all=True))
        self.assertEqual(result["transactions"], [])
        self.assertEqual(result["total_items"], 0)
        self.assertEqual(len(service.calls), 3)

    def test_failed_chunk_identifies_its_range_without_returning_partial_data(self):
        service = RecordingTransactionsService(fail_on_call=2)
        with self.assertRaises(PayPalTransactionsError) as context:
            asyncio.run(service.search_transactions(self.start, self.start + timedelta(days=90), fetch_all=True))
        self.assertEqual(context.exception.code, "paypal_api_error")
        self.assertIn("2026-01-31T00:00:00Z to 2026-03-02T00:00:00Z", context.exception.message)
        self.assertEqual(len(service.calls), 2)


if __name__ == "__main__":
    unittest.main()
