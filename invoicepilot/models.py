"""Invoice domain model and the PayPal Invoicing (v2) payload builder."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CENT = Decimal("0.01")
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "PLN", "CAD", "AUD", "JPY")


def to_money(value: Any) -> Decimal:
    """Quantize to cents, half-up (banker's rounding surprises invoices)."""
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value))
    return dec.quantize(CENT, rounding=ROUND_HALF_UP)


def format_amount(value: Decimal) -> str:
    return f"{to_money(value):.2f}"


@dataclass(frozen=True)
class Buyer:
    name: str
    email: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("buyer name must not be empty")

    def to_paypal(self) -> dict[str, Any]:
        billing: dict[str, Any] = {"name": {"full_name": self.name}}
        if self.email:
            billing["email_address"] = self.email
        return {"billing_info": billing}


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: Decimal
    unit_price: Decimal

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("line item description must not be empty")
        if Decimal(self.quantity) <= 0 or Decimal(self.unit_price) < 0:
            raise ValueError(f"invalid quantity/price for {self.description!r}")

    @property
    def amount(self) -> Decimal:
        return to_money(Decimal(self.quantity) * Decimal(self.unit_price))

    def to_paypal(self, currency: str) -> dict[str, Any]:
        qty = Decimal(self.quantity).normalize()
        qty_str = format(qty, "f")
        return {
            "name": self.description.strip(),
            "quantity": qty_str,
            "unit_amount": {"currency_code": currency, "value": format_amount(self.unit_price)},
        }


@dataclass
class Invoice:
    buyer: Buyer
    currency: str
    items: list[LineItem]
    issue_date: date
    due_date: date
    note: str = ""
    invoice_id: str = ""
    number: str = ""
    status: str = "DRAFT"
    paypal_id: str = ""
    request_id: str = ""
    links: dict[str, str] = field(default_factory=dict)

    @property
    def subtotal(self) -> Decimal:
        return to_money(sum((item.amount for item in self.items), Decimal("0")))

    @property
    def total(self) -> Decimal:
        # No tax/discount modelling in v1 — subtotal is the invoice total.
        return self.subtotal

    def canonical_payload(self) -> dict[str, Any]:
        """Stable subset used both for the API body and the idempotency id."""
        return {
            "detail": {
                "currency_code": self.currency,
                "invoice_number": self.number,
                "invoice_date": self.issue_date.isoformat(),
                "note": self.note,
                "payment_term": {
                    "term_type": "DUE_ON_DATE_SPECIFIED",
                    "due_date": self.due_date.isoformat(),
                },
            },
            "primary_recipients": [self.buyer.to_paypal()],
            "items": [item.to_paypal(self.currency) for item in self.items],
        }

    def idempotency_key(self) -> str:
        """Deterministic request id: same business content == same key."""
        body = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return f"invoicepilot-{digest[:32]}"

    def validate(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {self.currency!r}")
        if not self.items:
            raise ValueError("invoice needs at least one line item")
        if self.due_date < self.issue_date:
            raise ValueError("due_date must not precede issue_date")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.paypal_id or self.invoice_id,
            "invoice_id": self.invoice_id,
            "number": self.number,
            "status": self.status,
            "currency": self.currency,
            "total": format_amount(self.total),
            "buyer": self.buyer.name,
            "due_date": self.due_date.isoformat(),
            "line_items": len(self.items),
            "request_id": self.request_id,
        }

    @staticmethod
    def default_due_date(issue: date, days: int = 14) -> date:
        return issue + timedelta(days=days)
