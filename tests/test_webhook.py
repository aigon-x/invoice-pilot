import json

from invoicepilot.paypal import PayPalClient
from invoicepilot.store import Store
from invoicepilot.webhook import process_event


def _store(tmp_path):
    store = Store(tmp_path)
    store.upsert({"id": "INV2-TEST", "status": "SENT", "number": "INV-1"})
    return store


def test_offline_webhook_requires_explicit_signature(tmp_path):
    store = _store(tmp_path)
    event = {"event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "INV2-TEST"}}
    result = process_event(event, {}, PayPalClient(offline=True), store)
    assert result["verified"] is False
    assert result["applied"] is False
    assert store.get("INV2-TEST")["status"] == "SENT"


def test_offline_webhook_marks_invoice_paid(tmp_path):
    store = _store(tmp_path)
    event = {"event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "INV2-TEST"}}
    result = process_event(event, {"paypal-transmission-sig": "offline-valid"},
                           PayPalClient(offline=True), store)
    assert result["verified"] and result["applied"]
    assert store.get("INV2-TEST")["status"] == "PAID"


def test_unknown_event_is_verified_but_not_applied(tmp_path):
    store = _store(tmp_path)
    event = {"event_type": "INVOICING.INVOICE.UPDATED", "resource": {"id": "INV2-TEST"}}
    result = process_event(event, {"paypal-transmission-sig": "offline-valid"},
                           PayPalClient(offline=True), store)
    assert result["verified"] and not result["applied"]
