# InvoicePilot

Turn an unstructured order note into a PayPal invoice, then chase the payment:
create → send → remind on day 7 and day 14 → escalate to a human. Everything is
logged, nothing is invented.

```
Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h
```

→ `INV2-3F7F9A623B4E`, 2790.00 EUR, sent, with a reminder plan.

## What it is (and is not)

It is a small CLI + library: a strict parser, a PayPal Invoicing v2 client
(OAuth2 client_credentials, idempotent creates, bounded retries, timeouts), a
deterministic mock transport for offline runs, and a reminder agent with an
auditable decision log.

It is **not** a billing platform: no tax engine, no PDF rendering, no webhook
listener, no payment reconciliation beyond an on-demand status refresh.

## Run it

No installation, no dependencies (Python 3.11+ stdlib):

```bash
cd project
python3 -m invoicepilot --offline invoice create --text "$(cat <<'EOF'
Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h
EOF
)"
python3 -m invoicepilot --offline invoice status <id>
python3 -m invoicepilot --offline remind --dry-run
python3 -m invoicepilot --offline remind --execute --as-of 2026-10-27
```

`--offline` is always printed in the output (`MODE: OFFLINE`) and means: the
mock transport is used, no sockets are opened, state lives in `state/`.

Full scripted demo (tests + create + status + reminders, resets `demo-state/`):

```bash
bash run_demo.sh
```

### Live (PayPal sandbox)

```bash
export PAYPAL_CLIENT_ID=...
export PAYPAL_CLIENT_SECRET=...
python3 -m invoicepilot invoice create --file order.txt
python3 -m invoicepilot invoice status <id> --refresh
python3 -m invoicepilot remind --execute
```

Sandbox credentials come from <https://developer.paypal.com>. The default base
URL is `https://api-m.sandbox.paypal.com`; there is no silent fallback — with
missing keys the CLI exits `3` and tells you to set them or use `--offline`.

## Order grammar

One statement per line; blank lines and `#` comments are ignored.

| Line | Example |
|---|---|
| buyer | `Client: Acme Sp. z o.o. <billing@acme.example>` (also `Buyer:`, `Bill to:`, `Nabywca:`) |
| currency | `Currency: EUR` (or a symbol/code in a price: `@ 100 €`, `@ 100 USD`) |
| due date | `Due: 2026-11-05`, `Due: in 14 days`, `Termin: za 14 dni` |
| note | `Note: sprint 1` (joined into the invoice note) |
| item | `- 3 x Consulting @ 450.00`, `12h Development @ 120 EUR/h`, `2 szt. Wsparcie @ 200 zł` |
| declared total | `Total: 2790.00` (warned about if it disagrees with computed totals) |

Supported currencies: USD, EUR, GBP, PLN, CAD, AUD, JPY. Amounts accept
`1 200,50`, `1,200.50`, `450.00`, `450`. Quantities accept decimals.

All problems are reported in one pass, e.g.:

```
ERROR[parse]: nie udało się sparsować zlecenia
  - brak nabywcy — dodaj linię 'Client: <nazwa> [<email>]'
  - brak waluty — dodaj 'Currency: EUR' albo użyj symbolu w cenie
  - brak pozycji — format: '2 x Konsultacja @ 450.00 EUR'
```

## Reminders

| Day | Action | Notes |
|---|---|---|
| 0 | `send_invoice` | the invoice is created and sent |
| 7 | `remind` (level 1) | `POST .../remind` |
| 14 | `final_notice` (level 2) | last automatic reminder, sets `escalated` |
| 14+7 | `escalate_manual` (level 3) | no API call; logged once, a human decides |

Default is `--dry-run` (plan + log, no API calls, no credentials needed);
`--execute` performs the calls. Every decision — due or not, executed or not,
dry-run or not — is appended to `state/decisions.jsonl`.

## Files

```
invoicepilot/      library (parser, models, paypal client, transport, reminders, store, cli)
tests/             pytest suite, no network
state/             invoices.json + decisions.jsonl + mock_paypal.json (created at runtime)
```

The offline mock persists its invoices in `state/mock_paypal.json`, so
`remind --execute` works across CLI invocations without any network.

## Tests

```bash
python3 -m pytest -q      # 37 tests, < 2 s, zero network
```

Covered: parsing (formats, currency mismatch, collected errors), idempotency,
retry after 500/429/network error, timeout propagation, credential failure,
reminder schedule/escalation, dry-run immutability, CLI exit codes.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | order text could not be parsed |
| 3 | live mode without credentials |
| 4 | PayPal API error after retries |
| 5 | transport/network error |
| 6 | invoice not found in the local store |
| 7 | reminder error |

## Limitations (read before judging)

- Parser is regex-based and intentionally narrow; free-form prose beyond the
  grammar lands in warnings, not in guessed line items.
- No tax, discount, shipping or partial-payment modelling; totals = sum of items.
- The mock is not PayPal: it validates credentials/headers/shape and keeps
  state, but it does not reproduce PayPal's full error taxonomy.
- `invoice status --refresh` is a manual pull; there are no webhooks, so a
  PAID status is only visible after a refresh.
- State is a JSON file, not a database; single-writer CLI usage only.
- Money is `Decimal` with 2-decimal quantization; JPY is treated as 2-decimal
  for simplicity (real PayPal expects integer JPY amounts).

## License

MIT — see `LICENSE`.
