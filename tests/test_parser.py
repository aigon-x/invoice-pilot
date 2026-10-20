from datetime import date
from decimal import Decimal

import pytest

from invoicepilot.errors import OrderParseError
from invoicepilot.parser import parse_amount, parse_order, parse_order_with_warnings

ORDER = """Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h
Note: sprint 1
"""


def test_parses_items_buyer_currency_and_totals():
    invoice = parse_order(ORDER, issue_date=date(2026, 10, 20))
    assert invoice.buyer.name == "Acme Sp. z o.o."
    assert invoice.buyer.email == "billing@acme.example"
    assert invoice.currency == "EUR"
    assert invoice.due_date == date(2026, 11, 5)
    assert [item.description for item in invoice.items] == ["Konsultacja wdrozeniowa", "Development"]
    assert invoice.items[0].quantity == Decimal("3")
    assert invoice.subtotal == Decimal("2790.00")
    assert invoice.total == invoice.subtotal
    assert invoice.note == "sprint 1"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 200,50", Decimal("1200.50")),
        ("1,200.50", Decimal("1200.50")),
        ("450.00", Decimal("450.00")),
        ("450", Decimal("450")),
        ("0,5", Decimal("0.5")),
    ],
)
def test_parse_amount_handles_separators(raw, expected):
    assert parse_amount(raw) == expected


def test_parses_currency_from_symbol_and_defaults_due_date():
    text = "Bill to: Beta GmbH <ap@beta.example>\n2 x Audit @ 900 \u20ac\n"
    invoice, warnings = parse_order_with_warnings(text, issue_date=date(2026, 10, 20), default_due_days=10)
    assert invoice.currency == "EUR"
    assert invoice.due_date == date(2026, 10, 30)
    assert any("termin" in warning for warning in warnings)


def test_collects_every_problem_in_one_error():
    with pytest.raises(OrderParseError) as excinfo:
        parse_order("to jest wolny tekst bez danych")
    problems = excinfo.value.problems
    assert len(problems) >= 3
    assert any("nabywcy" in problem for problem in problems)
    assert any("waluty" in problem for problem in problems)
    assert any("pozycji" in problem for problem in problems)
    assert excinfo.value.exit_code == 2


def test_rejects_zero_quantity_and_due_before_issue():
    text = "Client: X <x@example.com>\nCurrency: USD\nDue: 2026-01-01\n0 x Consulting @ 100\n"
    with pytest.raises(OrderParseError) as excinfo:
        parse_order(text, issue_date=date(2026, 2, 1))
    joined = "\n".join(excinfo.value.problems)
    assert "ilo\u015b\u0107 musi by\u0107 > 0" in joined
    assert "przed dat\u0105 wystawienia" in joined


def test_currency_mismatch_is_reported():
    text = "Client: X <x@example.com>\nCurrency: EUR\n1 x Consulting @ 100 USD\n"
    with pytest.raises(OrderParseError) as excinfo:
        parse_order(text, issue_date=date(2026, 10, 20))
    assert any("nie zgadza si\u0119" in problem for problem in excinfo.value.problems)


def test_declared_total_mismatch_warns_but_uses_computed():
    text = "Client: X <x@example.com>\nCurrency: USD\n1 x A @ 100\nTotal: 90\n"
    invoice, warnings = parse_order_with_warnings(text, issue_date=date(2026, 10, 20))
    assert invoice.total == Decimal("100.00")
    assert any("r\u00f3\u017cni si\u0119" in warning for warning in warnings)


def test_unknown_currency_is_rejected_with_allowed_list():
    text = "Client: X <x@example.com>\nCurrency: XYZ\n1 x A @ 100\n"
    with pytest.raises(OrderParseError) as excinfo:
        parse_order(text, issue_date=date(2026, 10, 20))
    assert any("XYZ" in problem and "USD" in problem for problem in excinfo.value.problems)
