"""Server-side OAuth token handling for the PayPal Sandbox API."""

import asyncio
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load backend/.env for local development while preserving environment overrides.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

PAYPAL_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
TOKEN_URL = f"{PAYPAL_SANDBOX_BASE_URL}/v1/oauth2/token"
TRANSACTION_SEARCH_SCOPE = "https://uri.paypal.com/services/reporting/search/read"


class PayPalAuthError(Exception):
    """Raised when PayPal credentials cannot obtain a valid access token."""


class PayPalOAuthService:
    """Fetch and cache a Sandbox access token until shortly before expiry."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._scopes: set[str] = set()
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def has_cached_scope(self, scope: str) -> bool:
        """Inspect a scope on the current, unexpired cached token without fetching."""
        return bool(
            self._access_token
            and time.monotonic() < self._expires_at
            and scope in self._scopes
        )

    async def get_access_token(self) -> str:
        client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
        client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise PayPalAuthError("PayPal Sandbox credentials are not configured.")

        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token

        async with self._lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        TOKEN_URL,
                        auth=(client_id, client_secret),
                        data={"grant_type": "client_credentials"},
                        headers={
                            "Accept": "application/json",
                            "Accept-Language": "en_US",
                        },
                    )
            except httpx.HTTPError as exc:
                self._clear_token()
                raise PayPalAuthError("PayPal Sandbox could not be reached.") from exc

            if response.is_error:
                self._clear_token()
                raise PayPalAuthError("PayPal Sandbox rejected the credentials.")

            try:
                payload = response.json()
                access_token = payload["access_token"]
                expires_in = float(payload["expires_in"])
                scope_value = payload.get("scope", "")
                scopes = set(scope_value.split()) if isinstance(scope_value, str) else set()
                if not isinstance(access_token, str) or not access_token or expires_in <= 0:
                    raise ValueError("Invalid token response")
            except (ValueError, KeyError, TypeError):
                self._clear_token()
                raise PayPalAuthError("PayPal Sandbox returned an invalid token response.") from None

            self._access_token = access_token
            self._scopes = scopes
            # Refresh one minute early to avoid using a token at the expiry edge.
            self._expires_at = time.monotonic() + max(0.0, expires_in - 60.0)
            return access_token

    async def invalidate_access_token(self, access_token: str) -> None:
        """Forget a token rejected by a resource API so it can be refreshed."""
        async with self._lock:
            if self._access_token == access_token:
                self._clear_token()

    def _clear_token(self) -> None:
        self._access_token = None
        self._scopes.clear()
        self._expires_at = 0.0


paypal_oauth_service = PayPalOAuthService()
