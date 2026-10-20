"""PayPal Invoicing client: OAuth2, idempotency, retries, timeouts."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Callable

from .errors import ConfigurationError, PayPalAPIError, TransportError
from .models import Invoice
from .transport import HttpTransport, MockTransport, Response, Transport

MODE_LIVE = "live"
MODE_OFFLINE = "offline"
DEFAULT_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
RETRYABLE_STATUS = {0, 429, 500, 502, 503, 504}


class PayPalClient:
    """Small, explicit client. In live mode missing credentials fail loudly."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        offline: bool = False,
        transport: Transport | None = None,
        base_url: str = DEFAULT_SANDBOX_BASE,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.offline = offline
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self._sleep = sleeper
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.requests_made = 0

        if offline:
            self.mode = MODE_OFFLINE
            self.client_id = client_id or "offline-client"
            self.client_secret = client_secret or "offline-secret"
            self._transport: Transport = transport or MockTransport()
            return

        self.mode = MODE_LIVE
        resolved_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        resolved_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", resolved_id),
                ("PAYPAL_CLIENT_SECRET", resolved_secret),
            )
            if not value.strip()
        ]
        if missing:
            raise ConfigurationError(
                "tryb live wymaga po\u015bwiadcze\u0144 PayPal; brakuje: "
                + ", ".join(missing)
                + ". Ustaw je w \u015brodowisku (sandbox: developer.paypal.com) "
                "albo uruchom z --offline (mock transport, bez sieci)."
            )
        self.client_id = resolved_id
        self.client_secret = resolved_secret
        self._transport = transport or HttpTransport()

    # -- low level -------------------------------------------------------
    def _headers(self, *, bearer: str | None, request_id: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return headers

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        bearer: str | None = None,
        request_id: str | None = None,
        retry: bool = True,
    ) -> Response:
        url = f"{self.base_url}{path}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._headers(bearer=bearer, request_id=request_id)
        attempts = self.max_retries + 1 if retry else 1
        last_error = ""
        for attempt in range(attempts):
            self.requests_made += 1
            try:
                response = self._transport.request(method, url, headers, payload, self.timeout)
            except TransportError as exc:
                last_error = str(exc)
                if attempt + 1 >= attempts:
                    raise
                self._sleep(self._backoff(attempt))
                continue
            if response.status in RETRYABLE_STATUS and attempt + 1 < attempts:
                last_error = f"HTTP {response.status}"
                delay = self._retry_after(response, attempt)
                self._sleep(delay)
                continue
            return response
        raise TransportError(last_error or "request failed")

    def _backoff(self, attempt: int) -> float:
        return self.backoff_base * (2 ** attempt)

    def _retry_after(self, response: Response, attempt: int) -> float:
        raw = response.header("retry-after")
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
        return self._backoff(attempt)

    @staticmethod
    def _describe(response: Response) -> tuple[str, str]:
        try:
            data = response.json()
        except TransportError:
            data = {}
        issue = str(data.get("name") or data.get("error") or "")
        message = str(data.get("message") or data.get("error_description") or response.body[:200])
        return issue, message

    def _auth_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        body = b"grant_type=client_credentials"
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            self.requests_made += 1
            try:
                response = self._transport.request(
                    "POST", f"{self.base_url}/v1/oauth2/token", headers, body, self.timeout
                )
            except TransportError:
                if attempt + 1 >= attempts:
                    raise
                self._sleep(self._backoff(attempt))
                continue
            if response.status in RETRYABLE_STATUS and attempt + 1 < attempts:
                self._sleep(self._retry_after(response, attempt))
                continue
            if response.status != 200:
                issue, message = self._describe(response)
                raise PayPalAPIError(
                    f"OAuth2 token rejected (HTTP {response.status}): {message}", response.status, issue
                )
            data = response.json()
            self._token = str(data.get("access_token", ""))
            expires_in = float(data.get("expires_in", 3600))
            self._token_expires_at = time.monotonic() + max(30.0, expires_in - 60.0)
            return self._token
        raise TransportError("could not obtain OAuth2 token")

    # -- public API ------------------------------------------------------
    def create_invoice(self, invoice: Invoice, request_id: str | None = None) -> dict[str, Any]:
        invoice.validate()
        rid = request_id or invoice.idempotency_key()
        response = self._raw_request(
            "POST",
            "/v2/invoicing/invoices",
            body=invoice.canonical_payload(),
            bearer=self._auth_token(),
            request_id=rid,
        )
        if response.status not in (200, 201):
            issue, message = self._describe(response)
            raise PayPalAPIError(
                f"create_invoice failed (HTTP {response.status}): {message}", response.status, issue
            )
        record = response.json()
        invoice.request_id = rid
        invoice.paypal_id = str(record.get("id") or self._id_from_location(response))
        invoice.status = str(record.get("status") or "DRAFT")
        invoice.links = {
            link.get("rel", ""): link.get("href", "") for link in record.get("links", []) if link.get("rel")
        }
        return record

    def send_invoice(self, invoice_id: str, request_id: str | None = None) -> None:
        response = self._raw_request(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/send",
            body={"send_to_invoicer": True},
            bearer=self._auth_token(),
            request_id=request_id or f"send-{invoice_id}",
        )
        if response.status not in (200, 202, 204):
            issue, message = self._describe(response)
            raise PayPalAPIError(
                f"send_invoice failed (HTTP {response.status}): {message}", response.status, issue
            )

    def remind_invoice(self, invoice_id: str, request_id: str | None = None) -> None:
        response = self._raw_request(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/remind",
            body={},
            bearer=self._auth_token(),
            request_id=request_id or f"remind-{invoice_id}",
        )
        if response.status not in (200, 202, 204):
            issue, message = self._describe(response)
            raise PayPalAPIError(
                f"remind_invoice failed (HTTP {response.status}): {message}", response.status, issue
            )

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        response = self._raw_request(
            "GET", f"/v2/invoicing/invoices/{invoice_id}", bearer=self._auth_token()
        )
        if response.status == 404:
            raise PayPalAPIError(f"invoice {invoice_id} not found in PayPal", 404, "RESOURCE_NOT_FOUND")
        if response.status != 200:
            issue, message = self._describe(response)
            raise PayPalAPIError(
                f"get_invoice failed (HTTP {response.status}): {message}", response.status, issue
            )
        return response.json()

    @staticmethod
    def _id_from_location(response: Response) -> str:
        location = response.header("location") or response.header("paypal-request-id")
        return location.rstrip("/").rsplit("/", 1)[-1] if location else ""
