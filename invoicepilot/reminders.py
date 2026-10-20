"""Reminder agent: schedule day 0 / 7 / 14, escalation, auditable decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .errors import InvoicePilotError
from .paypal import PayPalClient
from .store import Store, merge_unique

SCHEDULE_DAYS = (0, 7, 14)
FINAL_DAY = 14
CLOSED_STATUSES = {"PAID", "CANCELLED", "REFUNDED", "MARKED_AS_PAID"}


@dataclass(frozen=True)
class ReminderAction:
    invoice_id: str
    day_offset: int
    kind: str  # send_invoice | remind | final_notice | escalate_manual
    level: int
    target_date: date
    due: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "day_offset": self.day_offset,
            "kind": self.kind,
            "level": self.level,
            "target_date": self.target_date.isoformat(),
            "due": self.due,
            "reason": self.reason,
        }


@dataclass
class Plan:
    invoice_id: str
    actions: list[ReminderAction] = field(default_factory=list)
    reason: str = ""
    next_due: str | None = None

    @property
    def due_actions(self) -> list[ReminderAction]:
        return [action for action in self.actions if action.due]

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "reason": self.reason,
            "next_due": self.next_due,
            "actions": [action.as_dict() for action in self.actions],
        }


class ReminderAgent:
    def __init__(
        self,
        client: PayPalClient | None = None,
        store: Store | None = None,
        *,
        clock: Callable[[], date] = date.today,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- planning --------------------------------------------------------
    def plan(self, record: dict[str, Any], as_of: date | None = None) -> Plan:
        today = as_of or self._clock()
        invoice_id = str(record.get("id", ""))
        status = str(record.get("status", "DRAFT")).upper()
        if status in CLOSED_STATUSES:
            return Plan(invoice_id=invoice_id, reason=f"status {status} \u2014 brak przypomnie\u0144")

        sent_raw = str(record.get("sent_on") or "")
        if not sent_raw:
            action = ReminderAction(
                invoice_id=invoice_id,
                day_offset=0,
                kind="send_invoice",
                level=0,
                target_date=today,
                due=True,
                reason="faktura jeszcze nie zosta\u0142a wys\u0142ana (dzie\u0144 0)",
            )
            return Plan(invoice_id=invoice_id, actions=[action], next_due=today.isoformat())

        sent_on = date.fromisoformat(sent_raw)
        days_since = (today - sent_on).days
        done = {int(day) for day in record.get("reminder_days_done", [])}
        actions: list[ReminderAction] = []
        for offset in (7, FINAL_DAY):
            if offset in done:
                continue
            target = sent_on + timedelta(days=offset)
            kind = "remind" if offset == 7 else "final_notice"
            level = 1 if offset == 7 else 2
            reason = (
                "przypomnienie 1 (dzie\u0144 7)"
                if offset == 7
                else "ostateczne wezwanie (dzie\u0144 14) + eskalacja do cz\u0142owieka"
            )
            actions.append(
                ReminderAction(
                    invoice_id=invoice_id,
                    day_offset=offset,
                    kind=kind,
                    level=level,
                    target_date=target,
                    due=today >= target,
                    reason=reason,
                )
            )
        if record.get("escalated") and not record.get("escalation_logged"):
            actions.append(
                ReminderAction(
                    invoice_id=invoice_id,
                    day_offset=FINAL_DAY,
                    kind="escalate_manual",
                    level=3,
                    target_date=sent_on + timedelta(days=FINAL_DAY + 7),
                    due=today >= sent_on + timedelta(days=FINAL_DAY + 7),
                    reason="po wezwaniu ostatecznym decyduje cz\u0142owiek \u2014 automat stoi",
                )
            )
        pending = sorted(action.target_date for action in actions if action.target_date >= today)
        day_word = "dzie\u0144" if days_since == 1 else "dni"
        return Plan(
            invoice_id=invoice_id,
            actions=actions,
            next_due=pending[0].isoformat() if pending else None,
            reason=f"{days_since} {day_word} od wys\u0142ania",
        )

    # -- execution -------------------------------------------------------
    def run(
        self,
        records: list[dict[str, Any]],
        *,
        as_of: date | None = None,
        dry_run: bool = True,
    ) -> list[dict[str, Any]]:
        today = as_of or self._clock()
        results: list[dict[str, Any]] = []
        for record in records:
            plan = self.plan(record, as_of=today)
            if not plan.actions:
                results.append({"invoice_id": record.get("id", ""), "action": "none", "reason": plan.reason})
                continue
            for action in plan.actions:
                outcome = self._apply(record, action, dry_run=dry_run)
                results.append(outcome)
        return results

    def _apply(self, record: dict[str, Any], action: ReminderAction, *, dry_run: bool) -> dict[str, Any]:
        invoice_id = action.invoice_id
        decision: dict[str, Any] = {
            "ts": self._now().isoformat(timespec="seconds"),
            "invoice_id": invoice_id,
            "action": action.kind,
            "level": action.level,
            "day_offset": action.day_offset,
            "due": action.due,
            "dry_run": dry_run,
            "executed": False,
            "reason": action.reason,
            "offline": bool(getattr(self.client, "offline", False)),
        }
        if not action.due:
            decision["reason"] = f"zaplanowane na {action.target_date.isoformat()}; jeszcze nie termin"
            self.store.append_decision(decision)
            return decision
        if action.kind == "escalate_manual":
            decision["reason"] = "eskalacja: wymagana decyzja cz\u0142owieka, brak akcji automatycznej"
            self.store.append_decision(decision)
            if not dry_run:
                record["escalation_logged"] = True
                record["updated_at"] = self._clock().isoformat()
                self.store.upsert(record)
            return decision
        if dry_run:
            decision["reason"] = f"dry-run: {action.reason}"
            self.store.append_decision(decision)
            return decision

        try:
            if action.kind == "send_invoice":
                self.client.send_invoice(invoice_id)
                record["sent_on"] = self._clock().isoformat()
                record["status"] = "SENT"
            else:
                self.client.remind_invoice(invoice_id, request_id=f"{action.kind}-{invoice_id}-{action.level}")
                record["reminders_sent"] = int(record.get("reminders_sent", 0)) + 1
                if action.kind == "final_notice":
                    record["escalated"] = True
            record["reminder_days_done"] = merge_unique(
                [str(day) for day in record.get("reminder_days_done", [])], [str(action.day_offset)]
            )
            record["updated_at"] = self._clock().isoformat()
            self.store.upsert(record)
            decision["executed"] = True
        except InvoicePilotError as exc:
            decision["error"] = str(exc)
            decision["reason"] = "b\u0142\u0105d wykonania \u2014 akcja pozostaje zaleg\u0142a"
        self.store.append_decision(decision)
        return decision


def plan_reminders(record: dict[str, Any], as_of: date | None = None) -> Plan:
    """Pure planning helper (no client, no store, no I/O)."""
    return ReminderAgent(clock=lambda: as_of or date.today()).plan(record, as_of=as_of)
