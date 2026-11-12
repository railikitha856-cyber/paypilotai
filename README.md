# PayPilot AI

PayPilot AI is a fintech operations assistant for small businesses. It provides a responsive React dashboard, a FastAPI service, PayPal Sandbox transaction retrieval, and Groq-powered categorization. The Overview financial analytics uses actual PayPal Sandbox results and clearly reports empty or unavailable data; remaining preview sections may still show labeled illustrative figures.

## Project structure

```text
paypilot-ai/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app and router registration
│   │   ├── routes/          # PayPal and AI endpoints
│   │   ├── schemas/         # Validated request / response models
│   │   └── services/        # OAuth, transaction search and Groq services
│   ├── .env.example         # Placeholder integration settings
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/      # Sidebar, cards, charts, table, header
│   │   ├── App.jsx
│   │   ├── index.css
│   │   └── main.jsx
│   ├── index.html
│   └── package.json
├── .gitignore
└── README.md
```

## Requirements

- Node.js 20 or newer and npm
- Python 3.10 or newer

## Start the frontend

In a terminal from the project root:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`).

## Start the backend

In a second terminal from the project root (Windows PowerShell):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`. Check `http://127.0.0.1:8000/health` or browse the interactive API docs at `http://127.0.0.1:8000/docs`.

The dashboard checks the backend health endpoint on load and every 15 seconds. It displays `Backend Connected` when the response matches the expected health payload and `Backend Disconnected` if the API is unavailable or returns an error. By default the frontend uses `http://localhost:8000`; set `VITE_API_BASE_URL` in `frontend/.env.local` to use another backend URL. Vite must be restarted after changing this value.

To verify the connection, start both servers, open the dashboard at `http://localhost:5173`, and confirm the status in the top bar. You can also open `http://127.0.0.1:8000/health`; it should return `{"status":"healthy","service":"PayPilot AI API"}`. Stop the backend to see the disconnected status.

On macOS/Linux, create and activate the virtual environment with `python3 -m venv .venv` and `source .venv/bin/activate`.

## Configuration

Copy `backend/.env.example` to `backend/.env` and add your PayPal Sandbox app credentials there. Keep all credentials on the backend; never put API keys in frontend source. The `.env.example` values are placeholders and must be replaced with credentials from your PayPal Developer Dashboard for authentication to connect.

## PayPal Sandbox authentication

Create a Sandbox REST app in the PayPal Developer Dashboard, then copy its client ID and secret into `backend/.env` (start from `backend/.env.example`). The backend loads this file locally; real environment variables take precedence. Keep the secret in the backend only. The OAuth service requests a Sandbox client-credentials token, caches it until shortly before expiry, and refreshes it as needed. The access token is never returned to the frontend.

Check `http://127.0.0.1:8000/api/paypal/status` after starting FastAPI. A successful credential check returns `{"status":"connected"}`. Missing or rejected credentials, and PayPal/network errors, return `{"status":"disconnected"}` without exposing credentials, tokens, or provider error details. To verify a successful connection, use real Sandbox app credentials; this project does not mock PayPal authentication.

## PayPal Sandbox transactions

The Transactions page calls the backend `GET /api/paypal/transactions` route. The backend authenticates through the existing OAuth service and requests `GET https://api-m.sandbox.paypal.com/v1/reporting/transactions` with `fields=transaction_info`. The route accepts timezone-aware RFC 3339 `start_date` and `end_date` values and returns normalized fields only; access tokens and raw provider errors are never sent to the browser. For example:

```text
http://127.0.0.1:8000/api/paypal/transactions?start_date=2026-10-01T00:00:00Z&end_date=2026-10-31T23:59:59Z
```

In the PayPal Developer Dashboard, select the Sandbox app connected to `backend/.env`, enable **Transaction search** under the app Features, and save. If the route returns a permission error, restart the backend to request a fresh token (or wait for the old token to expire) and retry. PayPal notes that executed transactions can take up to three hours to appear in Transaction Search. The Transactions page never substitutes sample rows when the API errors or returns no records.

### Inspect the cached Transaction Search scope

Open FastAPI Swagger UI at `http://127.0.0.1:8000/docs` and use `GET /api/paypal/debug-scope`. The endpoint inspects only the scope saved with the current in-memory OAuth token and returns `{"transaction_search_scope":true}` or `{"transaction_search_scope":false}`. It does not fetch a token or return token data. After enabling Transaction search, restart the backend, call `GET /api/paypal/status` once to obtain a fresh cached token, then call `GET /api/paypal/debug-scope`.

## AI transaction categorization

Add your Groq API key to `GROQ_API_KEY` in `backend/.env`, then restart FastAPI. The backend sends transaction details to Groq using `openai/gpt-oss-20b` with strict JSON Schema output and validates the response before returning it. The API key remains on the backend.

The Transactions page categorizes live Sandbox transactions when a transaction description is available. If none are available, it uses four illustrative examples, clearly marked as sample data rather than PayPal records. Select **Categorize transactions** to request categories, confidence scores, explanations, and income/refund/expense/unknown direction from Groq. AI results for live transactions are saved in the backend SQLite database and used by Financial Analytics. You can change a returned category manually in the current browser view. If Groq is not configured or a request fails, the page reports the error and does not substitute an AI result.

The endpoint accepts one transaction per request:

```json
{
  "description": "Online store customer order",
  "amount": "84.50",
  "currency": "USD",
  "transaction_type": "payment"
}
```

Use `POST http://127.0.0.1:8000/api/ai/categorize` with `Content-Type: application/json` to test it in Swagger UI at `http://127.0.0.1:8000/docs`.

If Groq rejects a request, the backend log records its HTTP status and normalized provider error type/code without logging the response body, API key, or secrets. API responses distinguish authentication failures, rate limits, unavailable models, invalid request/schema errors, and invalid model output.

## Financial analytics

The Overview page calls `GET /api/analytics/summary?start_date=...&end_date=...`. It uses the PayPal Sandbox Transaction Search service, requests all result pages, and aggregates successful incoming payments, refunds, fees, transaction counts, daily/monthly revenue, and saved AI categories. Requests longer than PayPal's 31-day limit are split into sequential windows of no more than 30 days; API timestamps are sent at whole-second precision and duplicate transaction IDs are removed before aggregation. If a window fails, the API error identifies its date range rather than returning partial totals. Currency totals are returned separately and the UI lets you select a currency; it never adds different currencies together. Pending and denied transactions are counted as transactions but excluded from successful revenue and fees. Refund event codes and fully reversed status are accounted for separately.

If PayPal returns no records, Overview displays an empty state. If Transaction Search is unavailable or permission is missing, it displays the API error. No demo figures are used for analytics.

Run the calculation and route-registration tests from `backend/`:

```bash
python -m unittest discover -s tests -v
```

In Swagger at `http://127.0.0.1:8000/docs`, call `GET /api/analytics/summary` with timezone-aware `start_date` and `end_date` query parameters. You can also open Overview in the frontend, choose **Last 7 days**, **Last 30 days**, **Last 90 days**, or a custom range, and use the currency selector when PayPal returns more than one currency. Transaction Search permission errors are surfaced without substituting sample data.

## Fraud risk assessments

Open **Fraud Detection** in the dashboard and choose **Analyze transactions**. `POST /api/fraud/analyze` accepts a timezone-aware `start_date` and `end_date`; omit both to analyze the last 30 days. The backend retrieves the requested records plus the preceding 30 days for historical comparison, then saves results to the same SQLite database. `GET /api/fraud/transactions` reads saved assessments. Use `PATCH /api/fraud/transactions/{transaction_id}/review` with `{"review_status":"approved"}`, `review`, or `escalated` to record a human review decision.

Scores come only from deterministic rules: an amount above 3× the historical median (at least five prior same-currency successful payments required; +35), at least five transactions in a 10-minute window (+35), three denied transactions within 60 minutes (+40), at least three refunds in seven days or refunds of at least 35% of 30-day incoming value with five sales (+35), a 24-hour frequency count above the larger of five or 3× historical daily average (+35, at least five prior records required), and a new event code after five same-currency historical records (+10). Scores are the sum of triggered rule weights, capped at 100; levels are Low 0–30, Medium 31–70, and High 71–100. Sparse amount or frequency history adds an explicit limitation and does not add risk points. Thresholds can be tuned through the `FRAUD_*` variables in `backend/.env.example`. Groq receives only triggered rule findings to produce explanatory prose; it cannot set scores, levels, or recommendations. Assessments are indicators for review, not confirmed fraud, and the app does not block or alter payments. Payer names and email addresses are not used by fraud rules or sent to Groq.

### Development-only synthetic fraud scenarios

`backend/tests/test_fraud_scenarios.py` contains in-memory **TEST DATA** cases for normal payments, unusually large payments, rapid payment clusters, repeated denials, and refund patterns. The suite calls the deterministic analyzer directly; it does not call PayPal or Groq, create PayPal records, or write results to SQLite. Run it from the `backend` directory:

```powershell
python -m unittest tests.test_fraud_scenarios -v
```

Expected assessments: normal payment **0 / Low / Approve**; large amount against five $100 historical payments **35 / Medium**; rapid cluster **35 / Medium**; three denied attempts **40 / Medium**; and three refunds in seven days **35 / Medium**. Each case also asserts a matching explanation.

Run backend tests, including risk-level scenarios, missing fields, duplicate persistence, and manual review updates, from `backend/`:

```powershell
python -m unittest discover -s tests -v
```

## AI Financial Assistant

The dashboard's **AI Assistant** page calls `POST /api/assistant/chat`. It retrieves actual PayPal Sandbox transactions for the date range in the question, delegates financial calculations to the existing analytics service, includes saved AI categories and saved fraud risk assessments, and asks Groq for a short qualitative explanation. Python-generated figures remain authoritative; the Groq explanation is rejected if it includes numeric or currency figures. Groq receives only a safe topic label and aggregate facts, never the original message, payer names, emails, or transaction IDs. Currencies are listed separately. No transaction data is replaced with sample data.

Example request:

```json
{
  "message": "How much revenue did I generate this month?",
  "conversation_id": null
}
```

The response includes a generated `conversation_id`, deterministic `answer` and `facts`, an optional qualitative `explanation`, the actual date range, and `data_available`. `ai_status` reports whether Groq contributed an explanation; when Groq is unavailable the endpoint still returns actual deterministic PayPal figures with a warning instead of failing the entire answer. The returned facts are account-specific; this is an empty-data example shape, not PayPal account data:

```json
{
  "conversation_id": "2f4b70cc-42b7-4a62-9f5f-63903440fcba",
  "answer": "No PayPal Sandbox transactions were returned for 2026-11-01 to 2026-11-12 UTC. There are no actual transaction figures to summarize for this period.",
  "explanation": null,
  "environment": "sandbox",
  "start_date": "2026-11-01T00:00:00Z",
  "end_date": "2026-11-12T12:00:00Z",
  "data_available": false,
  "ai_status": "not_needed",
  "warning": null,
  "facts": {
    "intent": "revenue",
    "date_range_utc": {"start": "2026-11-01T00:00:00+00:00", "end": "2026-11-12T12:00:00+00:00"},
    "data_available": false,
    "total_transaction_count": 0,
    "currencies": [],
    "fraud_assessments": {
      "assessments_found": 0,
      "risk_level_counts": {"High": 0, "Medium": 0, "Low": 0},
      "status": "risk assessments, not confirmed fraud"
    }
  }
}
```

Follow-up requests send the returned `conversation_id`; the previous topic and date period are retained within that active backend process when a follow-up does not specify a new period. This minimal context is kept in memory for up to two hours and cleared when requested from the UI (or when the backend restarts). To test, start both servers, open **AI Assistant**, then try the suggested revenue, refund, attention, expense, and performance questions. Follow one with “And what about refunds?” to check date context. Swagger at `http://127.0.0.1:8000/docs` can also call the endpoint. If PayPal returns no rows, the endpoint explicitly reports that no actual Sandbox figures are available.
#   p a y p i l o t a i  
 