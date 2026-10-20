"""Command line interface: `invoice create`, `invoice status`, `remind`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Any

from . import __version__
from .errors import (
    ConfigurationError,
    InvoiceNotFound,
    InvoicePilotError,
    OrderParseError,
)
from .ai import AiExtractor
from .models import format_amount
from .parser import parse_order_with_warnings
from .paypal import MODE_OFFLINE, PayPalClient
from .reminders import ReminderAgent
from .store import Store, invoice_record
from .webhook import process_event
from .transport import MockTransport

DEFAULT_STATE_DIR = "state"
TRUE_VALUES = {"1", "true", "yes", "on"}


def is_offline(args: argparse.Namespace) -> bool:
    env = os.environ.get("INVOICEPILOT_OFFLINE", "").strip().lower()
    return bool(args.offline) or env in TRUE_VALUES


def make_client(args: argparse.Namespace, store: Store) -> PayPalClient:
    """Offline client persists mock state, so `remind --execute` works across runs."""
    if is_offline(args):
        mock = MockTransport(state_path=str(store.root / "mock_paypal.json"))
        return PayPalClient(offline=True, transport=mock)
    return PayPalClient()


def banner(client: PayPalClient) -> str:
    if client.mode == MODE_OFFLINE:
        return (
            "MODE: OFFLINE \u2014 deterministyczny mock transportu PayPal, "
            "ZERO wywo\u0142a\u0144 sieciowych (dane tylko w lokalnym store)"
        )
    return f"MODE: LIVE \u2014 PayPal Invoicing API {client.base_url} (sandbox/live wg base URL)"


def emit(args: argparse.Namespace, payload: dict[str, Any], human_lines: list[str]) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for line in human_lines:
        print(line)


def read_text(args: argparse.Namespace) -> str:
    if args.text and args.file:
        raise OrderParseError("u\u017cyj --text ALBO --file, nie oba naraz")
    if args.file:
        if args.file == "-":
            return sys.stdin.read()
        with open(args.file, "r", encoding="utf-8") as handle:
            return handle.read()
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    raise OrderParseError(
        "brak tre\u015bci zlecenia",
        ["podaj --text \"...\" albo --file order.txt (albo wklej tekst na stdin)"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoicepilot",
        description="InvoicePilot \u2014 z nieustrukturyzowanego zlecenia do faktury PayPal.",
    )
    parser.add_argument("--version", action="version", version=f"invoicepilot {__version__}")
    parser.add_argument("--offline", action="store_true", help="mock transportu, zero sieci (jawnie oznaczone)")
    parser.add_argument("--store", default=os.environ.get("INVOICEPILOT_STATE", DEFAULT_STATE_DIR))
    parser.add_argument("--json", action="store_true", help="wyj\u015bcie maszynowe")
    subparsers = parser.add_subparsers(dest="group", required=True)

    invoice = subparsers.add_parser("invoice", help="operacje na fakturze")
    invoice_sub = invoice.add_subparsers(dest="command", required=True)

    create = invoice_sub.add_parser("create", help="sparsuj zlecenie, utw\u00f3rz i wy\u015blij faktur\u0119")
    create.add_argument("--text", help="tre\u015b\u0107 zlecenia")
    create.add_argument("--file", help="plik z tre\u015bci\u0105 (albo - na stdin)")
    create.add_argument("--issue-date", help="data wystawienia YYYY-MM-DD (domy\u015blnie dzi\u015b)")
    create.add_argument("--due-days", type=int, default=14, help="termin p\u0142atno\u015bci w dniach (domy\u015blnie 14)")
    create.add_argument("--no-send", action="store_true", help="utw\u00f3rz szkic bez wysy\u0142ki")
    create.add_argument("--ai", action="store_true",
                        help="wymu\u015b ekstrakcj\u0119 LLM (wymaga PAYPAL_AI_BASE_URL i PAYPAL_AI_API_KEY)")
    create.set_defaults(func=cmd_create)

    status = invoice_sub.add_parser("status", help="status faktury z lokalnego store (i z API w live)")
    status.add_argument("invoice_id")
    status.add_argument("--refresh", action="store_true", help="od\u015bwie\u017c status z PayPal (tylko live)")
    status.set_defaults(func=cmd_status)

    remind = subparsers.add_parser("remind", help="uruchom agenta przypomnie\u0144 (dzie\u0144 0/7/14)")
    remind.add_argument("--invoice", help="tylko jedna faktura")
    remind.add_argument("--as-of", help="data symulacji YYYY-MM-DD (do test\u00f3w i dry-run)")
    mode = remind.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="tylko plan i log decyzji (domy\u015blne)")
    mode.add_argument("--execute", action="store_true", help="realnie wysy\u0142aj przypomnienia")
    remind.set_defaults(func=cmd_remind)

    hook = subparsers.add_parser("webhook", help="obs\u0142u\u017c zdarzenie webhooka PayPal")
    hook.add_argument("--file", required=True, help="plik JSON z eventem")
    hook.add_argument("--headers", help="plik JSON z nag\u0142\u00f3wkami PayPal-*")
    hook.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                      help="mock transportu, zero sieci (globalna flaga te\u017c dzia\u0142a)")
    hook.set_defaults(func=cmd_webhook)
    return parser


def cmd_create(args: argparse.Namespace) -> int:
    text = read_text(args)
    extractor = AiExtractor()
    ai_mode = "RULE-BASED"
    if getattr(args, "ai", False) or extractor.available:
        extraction = extractor.extract(text)
        ai_mode = extraction.mode
        if extraction.mode == "LLM":
            text = extraction.to_grammar()
    issue = date.fromisoformat(args.issue_date) if args.issue_date else date.today()
    invoice, warnings = parse_order_with_warnings(text, issue_date=issue, default_due_days=args.due_days)
    store = Store(args.store)
    client = make_client(args, store)

    client.create_invoice(invoice)
    sent = not args.no_send
    if sent:
        client.send_invoice(invoice.paypal_id)
        invoice.status = "SENT"
    today = date.today()
    record = invoice_record(invoice, today, sent_on=today.isoformat() if sent else None)
    record["offline"] = client.mode == MODE_OFFLINE
    record["id"] = invoice.paypal_id or invoice.invoice_id
    record["total"] = format_amount(invoice.total)
    record["number"] = invoice.number
    store.upsert(record)

    payload = {"mode": client.mode, "ai_mode": ai_mode, "invoice": record,
               "warnings": warnings, "banner": banner(client)}
    human = [
        banner(client),
        f"invoice: id={record['id']} number={record['number']} total={record['total']} {invoice.currency}",
        f"buyer: {invoice.buyer.name}" + (f" <{invoice.buyer.email}>" if invoice.buyer.email else ""),
        f"due: {invoice.due_date.isoformat()} | items: {len(invoice.items)} | status: {record['status']}",
        f"request-id: {invoice.request_id} | ai: {ai_mode}",
    ]
    human.extend(f"WARN: {warning}" for warning in warnings)
    human.append(f"store: {store.invoices_path}")
    emit(args, payload, human)
    return 0


def _find_record(store: Store, key: str) -> dict[str, Any] | None:
    data = store.load()
    if key in data:
        return data[key]
    for record in data.values():
        if record.get("number") == key or record.get("invoice_id") == key:
            return record
    return None


def cmd_status(args: argparse.Namespace) -> int:
    store = Store(args.store)
    record = _find_record(store, args.invoice_id)
    if record is None:
        raise InvoiceNotFound(
            f"nie znam faktury {args.invoice_id!r} w store {store.invoices_path}; "
            "u\u017cyj id z 'invoice create' albo sprawd\u017a --store"
        )
    client = make_client(args, store)
    refreshed = False
    if args.refresh:
        if client.offline:
            raise ConfigurationError(
                "--refresh wymaga trybu live (PayPal API); w --offline status pochodzi z lokalnego store"
            )
        remote = client.get_invoice(str(record["id"]))
        record["status"] = str(remote.get("status", record.get("status")))
        record["amount"] = remote.get("amount", record.get("amount"))
        store.upsert(record)
        refreshed = True

    decisions = [d for d in store.decisions() if d.get("invoice_id") == record.get("id")][-5:]
    payload = {
        "mode": client.mode,
        "refreshed": refreshed,
        "invoice": record,
        "last_decisions": decisions,
        "banner": banner(client),
    }
    human = [
        banner(client),
        f"id: {record.get('id')} | number: {record.get('number')} | status: {record.get('status')}",
        f"total: {record.get('total')} {record.get('currency')} | due: {record.get('due_date')}",
        f"buyer: {record.get('buyer')} | sent_on: {record.get('sent_on') or '-'}",
        f"reminders_sent: {record.get('reminders_sent', 0)} | "
        f"days_done: {record.get('reminder_days_done', [])} | escalated: {record.get('escalated', False)}",
    ]
    if not refreshed:
        human.append("status: lokalny store (bez od\u015bwie\u017cenia z API)")
    for decision in decisions:
        human.append(
            f"  log {decision.get('ts')} {decision.get('action')} "
            f"executed={decision.get('executed')} dry_run={decision.get('dry_run')}"
        )
    emit(args, payload, human)
    return 0


def cmd_remind(args: argparse.Namespace) -> int:
    store = Store(args.store)
    data = store.load()
    if args.invoice and args.invoice not in data:
        raise InvoiceNotFound(f"nie znam faktury {args.invoice!r} w {store.invoices_path}")
    records = [data[args.invoice]] if args.invoice else list(data.values())
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    dry_run = not args.execute
    if dry_run and not is_offline(args):
        client = None
        mode_line = (
            "MODE: DRY-RUN \u2014 tylko plan i log decyzji, ZERO wywo\u0142a\u0144 API "
            "(po\u015bwiadczenia PayPal nie s\u0105 potrzebne)"
        )
    else:
        client = make_client(args, store)
        mode_line = banner(client) + (" | dry-run: bez wysy\u0142ki" if dry_run else "")
    agent = ReminderAgent(client, store, clock=lambda: as_of)
    results = agent.run(records, as_of=as_of, dry_run=dry_run)

    payload = {
        "mode": client.mode if client else "dry-run",
        "as_of": as_of.isoformat(),
        "dry_run": dry_run,
        "banner": mode_line,
        "decisions": results,
    }
    human = [
        mode_line,
        f"remind: as_of={as_of.isoformat()} dry_run={dry_run} invoices={len(records)}",
    ]
    if not records:
        human.append("brak faktur w store \u2014 nic do zrobienia")
    for result in results:
        if result.get("action") == "none":
            human.append(f"  {result.get('invoice_id')} brak akcji \u2014 {result.get('reason', '')}")
            continue
        human.append(
            f"  {result.get('invoice_id')} action={result.get('action')} level={result.get('level')} "
            f"executed={result.get('executed')} \u2014 {result.get('reason', '')}"
        )
        if result.get("error"):
            human.append(f"    error: {result['error']}")
    human.append(f"log decyzji: {store.decisions_path}")
    emit(args, payload, human)
    return 0


def _hoist_globals(argv: list[str]) -> list[str]:
    """Allow `invoicepilot invoice create --offline ...` (flags after subcommand)."""
    globals_: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("--offline", "--json"):
            globals_.append(token)
            index += 1
            continue
        if token == "--store" and index + 1 < len(argv):
            globals_.extend([token, argv[index + 1]])
            index += 2
            continue
        if token.startswith("--store="):
            globals_.append(token)
            index += 1
            continue
        rest.append(token)
        index += 1
    return globals_ + rest


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_hoist_globals(list(sys.argv[1:] if argv is None else argv)))
    try:
        return int(args.func(args))
    except OrderParseError as exc:
        print(f"ERROR[parse]: {exc}", file=sys.stderr)
        return exc.exit_code
    except ConfigurationError as exc:
        print(f"ERROR[config]: {exc}", file=sys.stderr)
        return exc.exit_code
    except InvoicePilotError as exc:
        print(f"ERROR[{type(exc).__name__}]: {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"ERROR[value]: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("przerwano", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


def cmd_webhook(args: argparse.Namespace) -> int:
    with open(args.file, encoding="utf-8") as fh:
        event = json.load(fh)
    headers = {}
    if args.headers:
        with open(args.headers, encoding="utf-8") as fh:
            headers = {str(k).lower(): str(v) for k, v in json.load(fh).items()}
    store = Store(args.store)
    client = make_client(args, store)
    result = process_event(event, headers, client, store)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verified"] else 4
