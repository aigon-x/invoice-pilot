"""Order extraction: LLM when configured, deterministic rules otherwise.

The rest of the pipeline still consumes the small documented grammar, so the AI layer's only
job is to turn messy prose into that grammar. Every result carries the mode it came from;
nothing silently pretends an LLM ran.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .parser import parse_order_with_warnings

SYSTEM_PROMPT = (
    "You extract invoice data. Reply with JSON only, no prose: "
    '{"buyer": str, "email": str|null, "currency": "USD|EUR|GBP|PLN", '
    '"items": [{"qty": number, "description": str, "unit_price": number}], "notes": str|null}. '
    "Use only values present in the text. If a field is missing, use null."
)


def _one_line(value: object) -> str:
    """A model must not be able to inject grammar lines through a field value."""
    return " ".join(str(value).split())


@dataclass
class Extraction:
    mode: str
    buyer: str | None
    email: str | None
    currency: str | None
    items: list[dict] = field(default_factory=list)
    notes: str | None = None

    def to_grammar(self) -> str:
        """Render the documented order grammar consumed by parser.parse_order."""
        lines = []
        buyer = self.buyer or "Client"
        lines.append(f"Client: {buyer}" + (f" <{self.email}>" if self.email else ""))
        if self.currency:
            lines.append(f"Currency: {self.currency}")
        for item in self.items:
            lines.append(f"{item['qty']} x {item['description']} @ {item['unit_price']:.2f} {self.currency or 'EUR'}")
        if self.notes:
            lines.append(f"Note: {self.notes}")
        return "\n".join(lines) + "\n"


class AiExtractor:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 20.0):
        self.base_url = (base_url or os.getenv("PAYPAL_AI_BASE_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("PAYPAL_AI_API_KEY", "")
        self.model = model or os.getenv("PAYPAL_AI_MODEL", "gpt-4o-mini")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def extract(self, text: str) -> Extraction:
        if self.available:
            try:
                return self._extract_llm(text)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
                pass  # fall through to rules; the mode field records the truth
        return self._extract_rules(text)

    def _extract_llm(self, text: str) -> Extraction:
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": text}],
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode())
        data = json.loads(payload["choices"][0]["message"]["content"])
        items = [{"qty": float(i["qty"]), "description": _one_line(i["description"]),
                  "unit_price": float(i["unit_price"])} for i in data.get("items", [])]
        if not items:
            raise ValueError("model returned no line items")
        return Extraction(mode="LLM", buyer=_one_line(data.get("buyer") or "") or None,
                          email=_one_line(data.get("email") or "") or None,
                          currency=data.get("currency"), items=items, notes=data.get("notes"))

    @staticmethod
    def _extract_rules(text: str) -> Extraction:
        invoice, _ = parse_order_with_warnings(text)
        return Extraction(
            mode="RULE-BASED",
            buyer=invoice.buyer.name or None,
            email=invoice.buyer.email or None,
            currency=invoice.currency,
            items=[{"qty": float(i.quantity), "description": i.description,
                    "unit_price": float(i.unit_price)} for i in invoice.items],
            notes=invoice.note or None,
        )
