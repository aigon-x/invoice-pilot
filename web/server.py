"""Offline web layer for InvoicePilot: one note in, one invoice card out.

The browser never talks to PayPal. Every request goes through the same
deterministic mock transport as ``--offline``, and its state lives under
``state/web/`` so the web demo and CLI runs stay separate.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from invoicepilot import __version__
from invoicepilot.ai import AiExtractor
from invoicepilot.errors import OrderParseError
from invoicepilot.models import format_amount
from invoicepilot.parser import parse_order_with_warnings
from invoicepilot.paypal import PayPalClient
from invoicepilot.store import Store, invoice_record
from invoicepilot.transport import MockTransport
from invoicepilot.webhook import process_event

WEB_DIR = Path(__file__).resolve().parent
DEFAULT_STATE = WEB_DIR.parent / "state" / "web"
OFFLINE_SIGNATURE = {"paypal-transmission-sig": "offline-valid"}

app = FastAPI(title="InvoicePilot", version=__version__)


class InvoiceRequest(BaseModel):
    text: str = Field(..., description="free-form order text")


class WebhookRequest(BaseModel):
    invoice_id: str | None = None
    event_type: str = "INVOICING.INVOICE.PAID"


def state_root() -> Path:
    """Read on every request so tests can point the app at a tmp directory."""
    return Path(os.environ.get("INVOICEPILOT_WEB_STATE", str(DEFAULT_STATE)))


def get_store() -> Store:
    return Store(state_root())


def get_client(store: Store) -> PayPalClient:
    mock = MockTransport(state_path=str(store.root / "mock_paypal.json"))
    return PayPalClient(offline=True, transport=mock)


def _items(invoice) -> list[dict[str, str]]:
    return [
        {
            "description": item.description,
            "quantity": format(item.quantity.normalize(), "f"),
            "unit_amount": format_amount(item.unit_price),
            "amount": format_amount(item.amount),
        }
        for item in invoice.items
    ]


def _find_record(store: Store, key: str | None) -> dict[str, Any] | None:
    data = store.load()
    if key:
        if key in data:
            return data[key]
        for record in data.values():
            if key in {record.get("id"), record.get("invoice_id"), record.get("number")}:
                return record
        return None
    if not data:
        return None
    # Insertion order is the store's recency: upsert appends new records.
    return next(reversed(data.values()))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    store = get_store()
    return {
        "status": "ok",
        "mode": "offline",
        "version": __version__,
        "invoices": len(store.load()),
        "store": str(store.root),
    }


@app.post("/api/invoice")
def create_invoice(payload: InvoiceRequest) -> dict[str, Any]:
    store = get_store()
    client = get_client(store)
    text = payload.text
    ai_mode = "RULE-BASED"
    extractor = AiExtractor()
    try:
        if extractor.available:
            extraction = extractor.extract(text)
            ai_mode = extraction.mode
            if extraction.mode == "LLM":
                text = extraction.to_grammar()
        invoice, warnings = parse_order_with_warnings(text)
    except OrderParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "order text could not be parsed", "problems": exc.problems},
        ) from exc

    client.create_invoice(invoice)
    client.send_invoice(invoice.paypal_id)
    invoice.status = "SENT"

    today = date.today()
    record = invoice_record(invoice, today, sent_on=today.isoformat())
    record["offline"] = True
    record["ai_mode"] = ai_mode
    store.upsert(record)

    return {
        "id": record["id"],
        "number": invoice.number,
        "total": format_amount(invoice.total),
        "currency": invoice.currency,
        "status": invoice.status,
        "items": _items(invoice),
        "ai_mode": ai_mode,
        "buyer": invoice.buyer.name,
        "due_date": invoice.due_date.isoformat(),
        "warnings": warnings,
    }


@app.post("/api/webhook")
def webhook(payload: WebhookRequest) -> dict[str, Any]:
    """Simulate a PayPal event with the explicit offline test signature."""
    store = get_store()
    record = _find_record(store, payload.invoice_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown invoice"})
    invoice_id = str(record.get("id") or record.get("invoice_id") or "")
    event = {"event_type": payload.event_type, "resource": {"id": invoice_id}}
    result = process_event(event, OFFLINE_SIGNATURE, get_client(store), store)
    if not result.get("verified"):
        raise HTTPException(status_code=400, detail={"error": "webhook signature not accepted"})
    if not result.get("applied"):
        raise HTTPException(
            status_code=400,
            detail={"error": f"event {payload.event_type} does not change status"},
        )
    return {
        "id": invoice_id,
        "status": result["status"],
        "verified": True,
        "applied": True,
        "event_type": result["event_type"],
    }
