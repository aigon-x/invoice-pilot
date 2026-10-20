"""Deterministic order-text parser.

The grammar is deliberately small and documented in README. Anything the
parser cannot interpret raises `OrderParseError` with every problem found in
one pass, so the user fixes the note once instead of line by line.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .errors import OrderParseError
from .models import Buyer, Invoice, LineItem, SUPPORTED_CURRENCIES

BUYER_RE = re.compile(
    r"^\s*(?:client|buyer|bill\s*to|customer|nabywca|klient)\s*[:\-]\s*(?P<rest>.+)$", re.I
)
CURRENCY_RE = re.compile(r"^\s*(?:currency|waluta)\s*[:\-]\s*(?P<code>[A-Za-z]{3})\b", re.I)
DUE_RE = re.compile(r"^\s*(?:due(?:\s*date)?|termin)\s*[:\-]\s*(?P<rest>.+)$", re.I)
NOTE_RE = re.compile(r"^\s*(?:note|notes|uwagi|opis)\s*[:\-]\s*(?P<rest>.+)$", re.I)
TOTAL_RE = re.compile(r"^\s*(?:total|suma|razem)\s*[:\-]\s*(?P<rest>.+)$", re.I)
ITEM_RE = re.compile(
    r"^\s*(?:[-*\u2022+]|\d+[.)])?\s*"
    r"(?P<qty>\d+(?:[.,]\d+)?)\s*"
    r"(?P<mult>x|\u00d7|\*|h|godz\.?|szt\.?|pcs|units?)?\s*"
    r"(?P<desc>.+?)\s*@\s*"
    r"(?P<price>.+?)\s*$",
    re.I,
)
EMAIL_RE = re.compile(r"[<\\(]?\b([\w.+-]+@[\w-]+\.[\w.-]+)\b[>\\)]?")
SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "z\u0142": "PLN", "zl": "PLN"}
DUE_IN_RE = re.compile(r"(?:in\s+|za\s+)?(\d+)\s*(?:days?|dni|dzie\u0144)", re.I)


def _clean_number(raw: str) -> str:
    text = raw.strip().replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace(" ", "").replace("'", "")
    return text


def parse_amount(raw: str) -> Decimal:
    """Parse '1 200,50', '1,200.50', '450.00', '450' into Decimal."""
    text = _clean_number(raw)
    text = re.sub(r"[^\d.,\-]", "", text)
    if not text or text in {"-", ".", ","}:
        raise InvalidOperation(raw)
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    elif has_comma:
        parts = text.split(",")
        text = text.replace(",", "") if len(parts) > 2 or len(parts[-1]) == 3 else text.replace(",", ".")
    elif has_dot:
        parts = text.split(".")
        if len(parts) > 2:
            text = text.replace(".", "")
    return Decimal(text)


def _extract_currency(token: str) -> str | None:
    upper = token.upper()
    for code in SUPPORTED_CURRENCIES:
        if re.search(rf"\b{code}\b", upper):
            return code
    for symbol, code in SYMBOLS.items():
        if symbol in token.lower():
            return code
    return None


def _parse_buyer(rest: str) -> Buyer:
    rest = rest.strip()
    email_match = EMAIL_RE.search(rest)
    email = email_match.group(1) if email_match else None
    name = rest[: email_match.start()] if email_match else rest
    name = name.strip(" <>()-,;")
    if not name:
        raise OrderParseError(
            "nie uda\u0142o si\u0119 odczyta\u0107 nabywcy",
            ["podaj nazw\u0119 obok e-maila, np. 'Client: Acme Sp. z o.o. <billing@acme.example>'"],
        )
    return Buyer(name=name, email=email)


def _parse_due(rest: str, issue: date) -> date:
    rest = rest.strip()
    try:
        return date.fromisoformat(rest[:10])
    except ValueError:
        pass
    match = DUE_IN_RE.search(rest)
    if match:
        return issue + timedelta(days=int(match.group(1)))
    raise OrderParseError(
        f"nie uda\u0142o si\u0119 odczyta\u0107 terminu p\u0142atno\u015bci: {rest!r}",
        ["u\u017cyj daty ISO (2026-11-05) albo 'in 14 days' / 'za 14 dni'"],
    )


def parse_order_with_warnings(
    text: str,
    issue_date: date | None = None,
    default_due_days: int = 14,
) -> tuple[Invoice, list[str]]:
    if not text or not text.strip():
        raise OrderParseError("opis zlecenia jest pusty", ["wklej tekst zlecenia lub u\u017cyj --file"])

    issue = issue_date or date.today()
    warnings: list[str] = []
    problems: list[str] = []
    buyer: Buyer | None = None
    currency: str | None = None
    items: list[LineItem] = []
    note_parts: list[str] = []
    due: date | None = None
    declared_total: Decimal | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if match := BUYER_RE.match(line):
            try:
                buyer = _parse_buyer(match.group("rest"))
            except OrderParseError as exc:
                problems.extend(exc.problems or [exc.message])
            continue
        if match := CURRENCY_RE.match(line):
            code = match.group("code").upper()
            if code not in SUPPORTED_CURRENCIES:
                problems.append(f"waluta {code} nieobs\u0142ugiwana (dozwolone: {', '.join(SUPPORTED_CURRENCIES)})")
            else:
                currency = code
            continue
        if match := DUE_RE.match(line):
            try:
                due = _parse_due(match.group("rest"), issue)
            except OrderParseError as exc:
                problems.extend(exc.problems or [exc.message])
            continue
        if match := NOTE_RE.match(line):
            note_parts.append(match.group("rest").strip())
            continue
        if match := TOTAL_RE.match(line):
            try:
                declared_total = parse_amount(match.group("rest"))
            except InvalidOperation:
                warnings.append(f"nie uda\u0142o si\u0119 odczyta\u0107 sumy z: {line!r}")
            continue
        if match := ITEM_RE.match(line):
            try:
                qty = parse_amount(match.group("qty"))
                price_token = match.group("price")
                price = parse_amount(price_token)
            except InvalidOperation:
                problems.append(f"nie uda\u0142o si\u0119 odczyta\u0107 liczby w pozycji: {line!r}")
                continue
            desc = match.group("desc").strip(" -:;,")
            found_currency = _extract_currency(price_token) or _extract_currency(match.group("mult") or "")
            if found_currency:
                if currency and currency != found_currency:
                    problems.append(
                        f"waluta pozycji ({found_currency}) nie zgadza si\u0119 z walut\u0105 faktury ({currency})"
                    )
                currency = currency or found_currency
            if not desc:
                problems.append(f"pozycja bez opisu: {line!r}")
                continue
            if qty <= 0:
                problems.append(f"ilo\u015b\u0107 musi by\u0107 > 0: {line!r}")
                continue
            try:
                items.append(LineItem(description=desc, quantity=qty, unit_price=price))
            except ValueError as exc:
                problems.append(str(exc))
            continue
        warnings.append(f"pomini\u0119ta linia (nie rozpoznano sk\u0142adni): {line!r}")

    if buyer is None:
        problems.append("brak nabywcy \u2014 dodaj lini\u0119 'Client: <nazwa> [<email>]'")
    if currency is None:
        problems.append("brak waluty \u2014 dodaj 'Currency: EUR' albo u\u017cyj symbolu w cenie")
    if not items:
        problems.append("brak pozycji \u2014 format: '2 x Konsultacja @ 450.00 EUR'")
    if due is None:
        due = issue + timedelta(days=default_due_days)
        warnings.append(f"brak terminu p\u0142atno\u015bci \u2014 przyj\u0119to +{default_due_days} dni ({due.isoformat()})")
    if due < issue:
        problems.append(f"termin p\u0142atno\u015bci {due} jest przed dat\u0105 wystawienia {issue}")

    if problems:
        raise OrderParseError("nie uda\u0142o si\u0119 sparsowa\u0107 zlecenia", problems)

    invoice = Invoice(
        buyer=buyer,
        currency=currency,
        items=items,
        issue_date=issue,
        due_date=due,
        note="; ".join(note_parts),
    )
    digest = hashlib.sha256(invoice.idempotency_key().encode("utf-8")).hexdigest()
    invoice.number = f"INV-{issue.strftime('%Y%m%d')}-{int(digest[:6], 16) % 10000:04d}"
    if declared_total is not None and declared_total != invoice.total:
        warnings.append(
            f"zadeklarowana suma {declared_total} r\u00f3\u017cni si\u0119 od wyliczonej {invoice.total}; "
            "u\u017cyto warto\u015bci wyliczonej z pozycji"
        )
    return invoice, warnings


def parse_order(text: str, issue_date: date | None = None, default_due_days: int = 14) -> Invoice:
    invoice, _ = parse_order_with_warnings(text, issue_date=issue_date, default_due_days=default_due_days)
    return invoice
