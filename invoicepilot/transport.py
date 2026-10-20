"""Pluggable HTTP transport: real (urllib, stdlib) and deterministic mock.

The mock is not a stub returning success blindly — it validates credentials,
headers and idempotency keys, keeps real state, and can be told to fail. That is
what makes the offline tests meaningful.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import TransportError


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError(f"response is not valid JSON: {exc}") from exc

    def header(self, name: str, default: str = "") -> str:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> Response: ...


class HttpTransport:
    """Thin urllib wrapper. Returns non-2xx as Response, raises only on network."""

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> Response:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(response.status, dict(response.headers.items()), response.read())
        except urllib.error.HTTPError as exc:
            payload = exc.read() if hasattr(exc, "read") else b""
            return Response(exc.code, dict(exc.headers.items()) if exc.headers else {}, payload or b"")
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise TransportError(f"{method} {url} failed: {exc}") from exc


class MockTransport:
    """In-memory PayPal sandbox. No sockets, deterministic ids, real validations."""

    def __init__(
        self,
        failures: dict[str, list[int]] | None = None,
        state_path: str | None = None,
    ) -> None:
        # failures maps "POST /v2/invoicing/invoices" -> [500, 429] consumed in order;
        # status 0 means "raise TransportError" (simulated connection reset).
        self._failures = {key: list(vals) for key, vals in (failures or {}).items()}
        self._invoices: dict[str, dict[str, Any]] = {}
        self._by_request_id: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []
        self._state_path = Path(state_path) if state_path else None
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._invoices = data.get("invoices", {})
        self._by_request_id = data.get("by_request_id", {})

    def _persist(self) -> None:
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"invoices": self._invoices, "by_request_id": self._by_request_id}, indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self._state_path)

    # -- helpers ---------------------------------------------------------
    @property
    def invoice_count(self) -> int:
        return len(self._invoices)

    @staticmethod
    def _route(method: str, url: str) -> str:
        path = url.split("?")[0]
        for prefix in ("https://api-m.sandbox.paypal.com", "https://api-m.paypal.com", "http://mock.local"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                break
        return f"{method.upper()} {path}"

    def _maybe_fail(self, route: str) -> Response | None:
        queue = self._failures.get(route)
        if not queue:
            return None
        status = queue.pop(0)
        if status == 0:
            raise TransportError(f"simulated connection reset for {route}")
        body = json.dumps({"name": "MOCK_FAILURE", "message": "injected", "status": status}).encode()
        headers = {"retry-after": "0"} if status == 429 else {}
        return Response(status, headers, body)

    # -- protocol --------------------------------------------------------
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> Response:
        route = self._route(method, url)
        self.requests.append({"route": route, "headers": dict(headers), "body": body, "timeout": timeout})
        if failure := self._maybe_fail(route):
            return failure

        if route == "POST /v1/oauth2/token":
            auth = headers.get("Authorization", "")
            if not auth.startswith("Basic ") or len(auth) <= 6:
                return Response(401, {}, b'{"error":"invalid_client"}')
            return Response(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {"access_token": "A21AA_mock_access_token", "expires_in": 32400, "token_type": "Bearer"}
                ).encode(),
            )

        payload: dict[str, Any] = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(400, {}, b'{"name":"INVALID_REQUEST","message":"bad json"}')

        if route == "POST /v2/invoicing/invoices":
            request_id = headers.get("PayPal-Request-Id", "")
            if not request_id:
                return Response(
                    400, {}, b'{"name":"MISSING_REQUEST_ID","message":"PayPal-Request-Id required"}'
                )
            if request_id in self._by_request_id:
                existing_id = self._by_request_id[request_id]
                record = self._invoices[existing_id]
                return Response(200, {"paypal-request-id": request_id}, json.dumps(record).encode())
            invoice_id = "INV2-" + hashlib.sha256(request_id.encode()).hexdigest()[:12].upper()
            amount = sum(
                float(item["quantity"]) * float(item["unit_amount"]["value"])
                for item in payload.get("items", [])
            )
            currency = payload.get("detail", {}).get("currency_code", "USD")
            record = {
                "id": invoice_id,
                "status": "DRAFT",
                "detail": payload.get("detail", {}),
                "primary_recipients": payload.get("primary_recipients", []),
                "items": payload.get("items", []),
                "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                "links": [{"rel": "self", "href": f"/v2/invoicing/invoices/{invoice_id}"}],
            }
            self._invoices[invoice_id] = record
            self._by_request_id[request_id] = invoice_id
            self._persist()
            headers_out = {"location": f"/v2/invoicing/invoices/{invoice_id}"}
            return Response(201, headers_out, json.dumps(record).encode())

        if route.startswith("POST /v2/invoicing/invoices/") and route.endswith("/send"):
            invoice_id = route.split("/")[4]
            record = self._invoices.get(invoice_id)
            if record is None:
                return Response(404, {}, b'{"name":"RESOURCE_NOT_FOUND"}')
            record["status"] = "SENT"
            self._persist()
            return Response(204, {}, b"")

        if route.startswith("POST /v2/invoicing/invoices/") and route.endswith("/remind"):
            invoice_id = route.split("/")[4]
            record = self._invoices.get(invoice_id)
            if record is None:
                return Response(404, {}, b'{"name":"RESOURCE_NOT_FOUND"}')
            record.setdefault("reminders_sent", 0)
            record["reminders_sent"] += 1
            self._persist()
            return Response(204, {}, b"")

        if route.startswith("GET /v2/invoicing/invoices/"):
            invoice_id = route.rsplit("/", 1)[-1]
            record = self._invoices.get(invoice_id)
            if record is None:
                return Response(404, {}, json.dumps({"name": "RESOURCE_NOT_FOUND"}).encode())
            return Response(200, {}, json.dumps(record).encode())

        return Response(404, {}, json.dumps({"name": "NOT_IMPLEMENTED", "route": route}).encode())
