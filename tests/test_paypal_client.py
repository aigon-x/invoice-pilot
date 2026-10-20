from datetime import date
from decimal import Decimal

import pytest

from invoicepilot.errors import ConfigurationError, PayPalAPIError, TransportError
from invoicepilot.models import Buyer, Invoice, LineItem
from invoicepilot.paypal import MODE_OFFLINE, PayPalClient
from invoicepilot.transport import MockTransport


def sample_invoice() -> Invoice:
    return Invoice(
        buyer=Buyer("Acme Sp. z o.o.", "billing@acme.example"),
        currency="USD",
        items=[
            LineItem("Consulting", Decimal("2"), Decimal("150.00")),
            LineItem("Support hours", Decimal("3"), Decimal("80.00")),
        ],
        issue_date=date(2026, 10, 20),
        due_date=date(2026, 11, 3),
    )


def offline_client(mock: MockTransport, **kwargs) -> PayPalClient:
    return PayPalClient(offline=True, transport=mock, sleeper=lambda _s: None, **kwargs)


def test_live_mode_without_credentials_fails_loudly(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(ConfigurationError) as excinfo:
        PayPalClient()
    message = str(excinfo.value)
    assert "PAYPAL_CLIENT_ID" in message
    assert "PAYPAL_CLIENT_SECRET" in message
    assert "--offline" in message
    assert excinfo.value.exit_code == 3


def test_live_mode_reports_only_the_missing_variable(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "abc")
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(ConfigurationError) as excinfo:
        PayPalClient()
    assert "PAYPAL_CLIENT_SECRET" in str(excinfo.value)
    assert "PAYPAL_CLIENT_ID," not in str(excinfo.value)


def test_create_invoice_is_idempotent_on_request_id():
    mock = MockTransport()
    client = offline_client(mock)
    invoice = sample_invoice()
    first = client.create_invoice(invoice)
    first_id = invoice.paypal_id
    second = client.create_invoice(invoice)
    assert second["id"] == first["id"] == first_id
    assert mock.invoice_count == 1
    create_requests = [r for r in mock.requests if r["route"] == "POST /v2/invoicing/invoices"]
    request_ids = {r["headers"]["PayPal-Request-Id"] for r in create_requests}
    assert len(create_requests) == 2
    assert request_ids == {invoice.idempotency_key()}


def test_same_content_produces_same_idempotency_key():
    first, second = sample_invoice(), sample_invoice()
    assert first.idempotency_key() == second.idempotency_key()
    second.items = list(second.items)
    second.items[0] = LineItem("Consulting", Decimal("2"), Decimal("151.00"))
    assert first.idempotency_key() != second.idempotency_key()


def test_retry_recovers_after_500_and_429():
    mock = MockTransport(failures={"POST /v2/invoicing/invoices": [500, 429]})
    slept: list[float] = []
    client = PayPalClient(offline=True, transport=mock, sleeper=slept.append, max_retries=3)
    invoice = sample_invoice()
    client.create_invoice(invoice)
    assert invoice.paypal_id.startswith("INV2-")
    assert len(slept) == 2
    assert mock.invoice_count == 1


def test_retry_exhaustion_raises_api_error_with_status():
    mock = MockTransport(failures={"POST /v2/invoicing/invoices": [503, 503, 503]})
    client = offline_client(mock, max_retries=2)
    with pytest.raises(PayPalAPIError) as excinfo:
        client.create_invoice(sample_invoice())
    assert excinfo.value.status == 503
    attempts = [r for r in mock.requests if r["route"] == "POST /v2/invoicing/invoices"]
    assert len(attempts) == 3


def test_network_error_is_retried_then_raised_as_transport_error():
    mock = MockTransport(failures={"POST /v2/invoicing/invoices": [0, 0]})
    client = offline_client(mock, max_retries=1)
    with pytest.raises(TransportError):
        client.create_invoice(sample_invoice())
    attempts = [r for r in mock.requests if r["route"] == "POST /v2/invoicing/invoices"]
    assert len(attempts) == 2


def test_timeout_is_applied_to_every_request():
    mock = MockTransport()
    client = PayPalClient(offline=True, transport=mock, timeout=2.5, sleeper=lambda _s: None)
    client.create_invoice(sample_invoice())
    assert all(request["timeout"] == 2.5 for request in mock.requests)


def test_oauth_token_is_cached_between_calls():
    mock = MockTransport()
    client = offline_client(mock)
    client.create_invoice(sample_invoice())
    with pytest.raises(PayPalAPIError):
        client.get_invoice("INV2-UNKNOWN")
    token_calls = [r for r in mock.requests if r["route"] == "POST /v1/oauth2/token"]
    assert len(token_calls) == 1


def test_send_and_remind_update_mock_status():
    mock = MockTransport()
    client = offline_client(mock)
    invoice = sample_invoice()
    client.create_invoice(invoice)
    client.send_invoice(invoice.paypal_id)
    client.remind_invoice(invoice.paypal_id)
    record = client.get_invoice(invoice.paypal_id)
    assert record["status"] == "SENT"
    assert record["reminders_sent"] == 1


def test_offline_mode_is_explicitly_reported():
    client = offline_client(MockTransport())
    assert client.mode == MODE_OFFLINE
    assert client.offline is True
