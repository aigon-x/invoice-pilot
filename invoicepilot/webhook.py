"""Webhook handling: verify with PayPal, then move the invoice to its new status."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .paypal import PayPalClient
from .store import Store

STATUS_BY_EVENT = {
    "INVOICING.INVOICE.PAID": "PAID",
    "INVOICING.INVOICE.CANCELLED": "CANCELLED",
    "INVOICING.INVOICE.REFUNDED": "REFUNDED",
    "INVOICING.INVOICE.SCHEDULED": "SCHEDULED",
}


def process_event(event: dict[str, Any], headers: dict[str, str], client: PayPalClient,
                  store: Store) -> dict[str, Any]:
    verified = client.verify_webhook_signature(headers, event)
    event_type = str(event.get("event_type", ""))
    resource = event.get("resource") or {}
    invoice_id = resource.get("id") or resource.get("invoice_id") or ""
    target = STATUS_BY_EVENT.get(event_type)
    applied = False
    if verified and target and invoice_id:
        record = _find(store, invoice_id)
        if record is not None:
            record["status"] = target
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            store.upsert(record)
            applied = True
    result = {"verified": verified, "event_type": event_type, "invoice_id": invoice_id,
              "status": target if verified else None, "applied": applied}
    store.append_decision({"ts": datetime.now(timezone.utc).isoformat(), "kind": "webhook",
                           **result})
    return result


def _find(store: Store, invoice_id: str) -> dict[str, Any] | None:
    for record in store.load().values():
        if invoice_id in {record.get("id"), record.get("paypal_id"), record.get("number")}:
            return dict(record)
    return None
