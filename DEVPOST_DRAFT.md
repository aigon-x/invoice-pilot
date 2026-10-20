# DEVPOST DRAFT — InvoicePilot

## Name
InvoicePilot

## Elevator pitch
Paste a messy order note; InvoicePilot turns it into a PayPal invoice, sends it,
and chases payment on day 7 and day 14 — with an auditable log of every decision.

## Inspiration
Freelancers and small studios write orders in notes, chats and e-mails. Turning
those into invoices is manual, and chasing late payments is worse: it is
emotional, easy to postpone, and usually undocumented. We wanted a tool that
does the boring, repetitive part and shows its work — not a black box that
"handles billing".

## What it does
- Parses an unstructured order (buyer, currency, due date, line items with
  quantity and unit price) and validates it, reporting all problems at once.
- Creates the invoice through the PayPal Invoicing API, sends it, and keeps the
  invoice id and request id.
- Is idempotent: the same business content produces the same
  `PayPal-Request-Id`, so retries and double-clicks never create twin invoices.
- Retries `429/5xx` and network failures with exponential backoff, bounded by
  `max_retries`, with a timeout on every request.
- Runs an agent that plans reminders on day 0/7/14, sends them, and escalates to
  a human after the final notice. Every decision — including "not due yet" and
  "dry run" — lands in a JSONL log.
- Works fully offline against a deterministic mock transport, clearly labelled
  `MODE: OFFLINE`, so demos and tests never need network access.

## How we built it
Python 3.12, standard library only. Six small modules: `parser`, `models`,
`paypal` (OAuth2 client_credentials + Invoicing v2), `transport` (real urllib
transport and an in-memory mock), `reminders` (schedule + escalation), `store`
(atomic JSON), plus a `cli`. Money is `Decimal` quantized half-up. The mock is
not a stub: it validates Basic auth, requires `PayPal-Request-Id`, stores
invoices, applies send/remind transitions, persists across CLI runs, and can
inject 500/429/network failures so the retry path is tested for real.

## Challenges
- Idempotency that survives retries *and* process restarts. Solved by deriving
  the request id from a canonical payload hash instead of a random UUID.
- Keeping offline mode honest. The tempting shortcut is to skip the API and
  return success; instead the mock implements the protocol subset we use and
  fails loudly on bad credentials or missing headers.
- Making the reminder agent idempotent: it derives actions from `sent_on` and
  `reminder_days_done` rather than from timers, so running it twice does nothing
  twice.
- Number formats: `1 200,50` and `1,200.50` in the same parser without guessing
  wrong. Ambiguity is documented, not hidden.
- A negative path that is genuinely readable: missing live credentials must say
  exactly which variables are missing and how to switch to `--offline`.

## Accomplishments
- 37 tests passing in under a second, zero network, including negative cases
  (missing credentials, parse failure, exhausted retries, network error).
- A CLI that returns stable exit codes (0/2/3/4/5/6/7) and never prints
  "success" for work it did not do.
- A reproducible offline demo: one command creates the invoice, then the
  reminder timeline can be replayed with `--as-of`.
- Documentation that states limitations up front (no tax engine, no webhooks,
  manual status refresh, JSON store).

## What we learned
- The hard part of payments is not the happy path; it is retries, duplicates and
  "what state is this invoice actually in".
- A test double that validates the protocol catches more bugs than one that
  returns canned JSON.
- Audit logs are a product feature, not a debugging leftover — the decision log
  is what makes an automated reminder agent trustworthy.

## What's next
- Real sandbox verification and a recorded demo with PayPal sandbox credentials.
- Webhook listener for payment events, so status is pushed instead of polled.
- Pluggable payment rails behind the same `Transport`/client interface.
- Tax/discount support and a PDF/HTML invoice preview.
- Optional LLM extraction fallback for prose orders the strict grammar rejects —
  with the same validation and no silent guessing.

## Built with
Python 3.12 · PayPal Invoicing API v2 (OAuth2 client_credentials, create/send/get/remind)
· urllib (stdlib) · decimal · argparse · pytest · JSONL audit log

## Links
- Repository: `<REPO_URL>` (public, MIT)
- Demo video: `<VIDEO_URL>`
- PayPal developer sandbox: <https://developer.paypal.com>

## How to run (judges)
```bash
cd project
python3 -m pytest -q                      # 37 tests, no network
cat > order.txt <<'EOF'
Client: Acme <ap@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Consulting @ 450.00
EOF
python3 -m invoicepilot --offline invoice create --file order.txt
python3 -m invoicepilot --offline invoice status <id>
python3 -m invoicepilot --offline remind --dry-run --as-of 2026-10-27
```

Live sandbox run requires `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`; without
them the CLI exits with code 3 and a readable message (no silent fallback).
