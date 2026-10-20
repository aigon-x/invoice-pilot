from datetime import date
from decimal import Decimal

from invoicepilot.models import Buyer, Invoice, LineItem
from invoicepilot.paypal import PayPalClient
from invoicepilot.reminders import ReminderAgent, plan_reminders
from invoicepilot.store import Store
from invoicepilot.transport import MockTransport

SENT_ON = date(2026, 10, 20)


def build(tmp_path):
    store = Store(tmp_path / "state")
    mock = MockTransport(state_path=str(tmp_path / "mock.json"))
    client = PayPalClient(offline=True, transport=mock, sleeper=lambda _s: None)
    invoice = Invoice(
        buyer=Buyer("Acme", "ap@acme.example"),
        currency="EUR",
        items=[LineItem("Consulting", Decimal("1"), Decimal("1000.00"))],
        issue_date=SENT_ON,
        due_date=date(2026, 11, 3),
    )
    client.create_invoice(invoice)
    return store, client, invoice


def record_for(invoice, *, sent_on: str = "", status: str = "DRAFT") -> dict:
    return {
        "id": invoice.paypal_id,
        "number": invoice.number or "INV-20261020-0001",
        "status": status,
        "currency": invoice.currency,
        "total": "1000.00",
        "buyer": invoice.buyer.name,
        "due_date": invoice.due_date.isoformat(),
        "sent_on": sent_on,
        "reminder_days_done": [],
        "escalated": False,
    }


def test_unsent_invoice_gets_send_action_on_day_zero(tmp_path):
    store, client, invoice = build(tmp_path)
    agent = ReminderAgent(client, store, clock=lambda: SENT_ON)
    plan = agent.plan(record_for(invoice))
    assert [action.kind for action in plan.actions] == ["send_invoice"]
    assert plan.actions[0].due is True


def test_waits_before_day_seven(tmp_path):
    store, client, invoice = build(tmp_path)
    record = record_for(invoice, sent_on="2026-10-20", status="SENT")
    plan = plan_reminders(record, as_of=date(2026, 10, 26))
    assert plan.next_due == "2026-10-27"
    assert [action.due for action in plan.actions] == [False, False]


def test_execute_full_schedule_then_escalation(tmp_path):
    store, client, invoice = build(tmp_path)
    record = record_for(invoice)
    store.upsert(record)

    day0 = ReminderAgent(client, store, clock=lambda: SENT_ON)
    result = day0.run([record], as_of=SENT_ON, dry_run=False)
    assert result[0]["executed"] is True
    assert record["sent_on"] == SENT_ON.isoformat()
    assert record["status"] == "SENT"

    day7 = ReminderAgent(client, store, clock=lambda: date(2026, 10, 27))
    result7 = day7.run([record], as_of=date(2026, 10, 27), dry_run=False)
    executed = [entry for entry in result7 if entry["executed"]]
    assert [entry["action"] for entry in executed] == ["remind"]
    assert record["reminders_sent"] == 1

    day14 = ReminderAgent(client, store, clock=lambda: date(2026, 11, 3))
    result14 = day14.run([record], as_of=date(2026, 11, 3), dry_run=False)
    assert any(entry["action"] == "final_notice" and entry["executed"] for entry in result14)
    assert record["escalated"] is True

    day22 = ReminderAgent(client, store, clock=lambda: date(2026, 11, 11))
    result22 = day22.run([record], as_of=date(2026, 11, 11), dry_run=False)
    escalation = [entry for entry in result22 if entry["action"] == "escalate_manual"]
    assert escalation and escalation[0]["executed"] is False
    assert record["escalation_logged"] is True

    again = day22.run([record], as_of=date(2026, 11, 11), dry_run=False)
    assert [entry for entry in again if entry["action"] == "escalate_manual"] == []

    decisions = store.decisions()
    assert len(decisions) >= 4
    assert all("reason" in entry and "ts" in entry for entry in decisions)


def test_paid_invoice_gets_no_actions(tmp_path):
    store, client, invoice = build(tmp_path)
    record = record_for(invoice, sent_on="2026-10-20", status="PAID")
    agent = ReminderAgent(client, store, clock=lambda: date(2026, 11, 11))
    plan = agent.plan(record, as_of=date(2026, 11, 11))
    assert plan.actions == []
    assert "PAID" in plan.reason


def test_dry_run_logs_decisions_without_touching_state(tmp_path):
    store, client, invoice = build(tmp_path)
    record = record_for(invoice, sent_on="2026-10-20", status="SENT")
    agent = ReminderAgent(client, store, clock=lambda: date(2026, 10, 27))
    before = dict(record)
    results = agent.run([record], as_of=date(2026, 10, 27), dry_run=True)
    executed = [entry for entry in results if entry["action"] == "remind"]
    assert executed and executed[0]["executed"] is False
    assert executed[0]["dry_run"] is True
    assert record == before
    assert store.decisions()[0]["dry_run"] is True
    assert client._transport.invoice_count == 1


def test_plan_reminders_is_pure_and_needs_no_client():
    record = {
        "id": "INV2-X",
        "status": "SENT",
        "sent_on": "2026-10-20",
        "reminder_days_done": [],
    }
    plan = plan_reminders(record, as_of=date(2026, 11, 4))
    kinds = [action.kind for action in plan.actions if action.due]
    assert kinds == ["remind", "final_notice"]
