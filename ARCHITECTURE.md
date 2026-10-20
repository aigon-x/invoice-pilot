# Architecture

## Data flow

```
order text ──► parser.py ──► Invoice (models.py)
                                │
                                ├─ canonical_payload() ──► paypal.py ──► transport.py ──► PayPal
                                │        (deterministic)      (OAuth2,          │
                                │                              retry,          ├─ HttpTransport (urllib)
                                │                              request-id)     └─ MockTransport (offline)
                                │
                                └─ invoice_record() ──► store.py (invoices.json, decisions.jsonl)
                                                              ▲
reminders.py ── plan(record, as_of) ──► actions day 0/7/14 ────┘
      │                                   │
      └─ decision log (JSONL) ◄───────────┘  (dry-run or execute)
```

## Modules

| Module | Responsibility | Deliberately not responsible for |
|---|---|---|
| `parser.py` | grammar → `Invoice`, collect all problems at once | guessing intent, currency conversion |
| `models.py` | money quantization, totals, PayPal payload, idempotency key | I/O |
| `paypal.py` | token cache, request-id, retry/backoff, timeouts, error mapping | parsing business text |
| `transport.py` | HTTP boundary (`Response`), offline mock with real state | know about invoices/domain |
| `reminders.py` | schedule, escalation, decision log | sending emails itself |
| `store.py` | atomic JSON persistence | concurrency, migrations |
| `cli.py` | arguments, exit codes, human/JSON output | business rules |

## Key decisions

**Idempotency by content hash.** The `PayPal-Request-Id` is
`invoicepilot-<sha256(canonical payload)[:32]>`. Re-running `create` with the
same buyer, items, currency and dates produces the same key, so PayPal dedupes
instead of creating a twin invoice. Alternative: random UUID stored before the
call — safer against duplicates on *retry*, but useless when the process dies
between generate and send, and it hides accidental double submissions.

**Retry policy at the transport boundary, not in business code.** Only
`429/5xx` and network errors are retried, with exponential backoff, honoring
`Retry-After`. `4xx` (other than 429) is returned immediately — retrying a
validation error just burns quota. Every retry is bounded by `max_retries` and
every attempt carries the same request id, so retries are safe.

**Mock as a first-class transport, not a stub.** `MockTransport` validates
Basic auth on the token endpoint, requires `PayPal-Request-Id`, stores created
invoices, applies send/remind state transitions, and can inject failures
(`failures={"POST /v2/invoicing/invoices": [500, 429]}`). That makes the offline
tests exercise the real client logic (retry, idempotency, 404 handling) instead
of asserting that a fake returned `True`. Alternative: `unittest.mock` patches —
rejected because patches encode the implementation, not the protocol.

**Explicit offline mode.** Live mode fails closed on missing credentials
(exit 3) and never falls back to mock. Mixing them silently is how demo data
ends up in production books.

**Reminder state machines over cron tables.** The agent derives actions from
`sent_on` and `reminder_days_done`, so it is idempotent: running it twice on the
same day re-plans nothing already done. `--as-of` lets tests and demos travel in
time without touching the system clock.

**Atomic JSON store.** `os.replace` after fsync prevents a half-written
`invoices.json` if the process dies mid-write. SQLite would be the next step
when multi-writer or history is needed; for a single-operator CLI the file is
easier to inspect and to diff.

## Failure semantics

- PayPal 4xx → `PayPalAPIError` (exit 4), no state change.
- Network exhausted retries → `TransportError` (exit 5), no state change.
- Reminder execution failure → decision logged with `error`, action stays
  pending, next run retries it (no lost reminders, no duplicate sends where
  PayPal request ids are stable).
- Parse failure → exit 2 with every problem listed; nothing is created.

## Extension points

- New payment rail: implement `Transport` (or a new client with the same
  interface) — reminders call `send_invoice`/`remind_invoice` only.
- Tax/discount: add fields to `Invoice.canonical_payload()` and totals; parser
  grammar is isolated in `ITEM_RE`/`TOTAL_RE`.
- Webhooks: add a `refresh_status(record)` path that updates `store` from
  PayPal events; the reminder planner already reads status from the record.
