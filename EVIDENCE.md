# EVIDENCE — InvoicePilot

Pomiar: **2026-10-20T10:22:45Z**, host `local-aigon`, katalog projektu
`.../hakaton_devpost_paypal-ai-hackathon/project/`.

Środowisko (zmierzone):

```
$ /usr/bin/python3 -VV
Python 3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]

$ /usr/bin/python3 -m pytest --version
pytest 9.0.3
```

Sieć: **zero wywołań** — wszystkie dowody poniżej powstały w trybie `--offline`
(mock transportu). Nie wysłano żadnego żądania do PayPal.

Uwaga o szumie: linia `(Linux uses --headless by default...)` w wyjściu pytest
pochodzi z hooka `sitecustomize` hosta, nie z tego projektu.

## 1. Testy (37 passed)

```
$ /usr/bin/python3 -m pytest -q
.....................................[100%]
37 passed in 0.88s
```

Zakres: parser (formaty kwot, waluta z symbolu, zebrane błędy, mismatch walut,
ujemna/zerowa ilość, termin przed wystawieniem, suma zadeklarowana), klient
PayPal (brak poświadczeń, idempotencja request-id, retry 500/429, wyczerpanie
retry, błąd sieci, timeout przekazany do transportu, cache tokenu, send/remind),
agent przypomnień (dzień 0/7/14, eskalacja, dry-run bez mutacji, faktura
opłacona), CLI (kody wyjścia 0/2/3/6, flagi po podkomendzie, dry-run domyślny,
execute aktualizuje stan i log).

## 2. Demo: create offline (id + kwota)

```
$ python3 -m invoicepilot --offline --store demo-state invoice create --text "Client: Acme Sp. z o.o. <billing@acme.example>
Currency: EUR
Due: 2026-11-05
- 3 x Konsultacja wdrozeniowa @ 450.00
- 12h Development @ 120.00 EUR/h"

MODE: OFFLINE — deterministyczny mock transportu PayPal, ZERO wywołań sieciowych (dane tylko w lokalnym store)
invoice: id=INV2-3F7F9A623B4E number=INV-20261020-0154 total=2790.00 EUR
buyer: Acme Sp. z o.o. <billing@acme.example>
due: 2026-11-05 | items: 2 | status: SENT
request-id: invoicepilot-6a478cdace9d6a2b7925b5c634479509
store: demo-state/invoices.json
```

## 3. Idempotencja widoczna z CLI

Ten sam tekst, drugie uruchomienie, ten sam request-id i to samo id:

```
$ python3 -m invoicepilot --offline --store demo-state invoice create --text "<ten sam tekst>"
invoice: id=INV2-3F7F9A623B4E number=INV-20261020-0154 total=2790.00 EUR
request-id: invoicepilot-6a478cdace9d6a2b7925b5c634479509

$ python3 -c "import json;d=json.load(open('demo-state/mock_paypal.json'));print('invoices in mock:',len(d['invoices']))"
invoices in mock: 1
```

`tests/test_paypal_client.py::test_create_invoice_is_idempotent_on_request_id`
dodatkowo sprawdza, że oba żądania niosą identyczny nagłówek
`PayPal-Request-Id` i że powstała dokładnie jedna faktura.

## 4. Status z lokalnego store

```
$ python3 -m invoicepilot --offline --store demo-state invoice status INV2-3F7F9A623B4E

MODE: OFFLINE — deterministyczny mock transportu PayPal, ZERO wywołań sieciowych (dane tylko w lokalnym store)
id: INV2-3F7F9A623B4E | number: INV-20261020-0154 | status: SENT
total: 2790.00 EUR | due: 2026-11-05
buyer: Acme Sp. z o.o. | sent_on: 2026-10-20
reminders_sent: 0 | days_done: [] | escalated: False
status: lokalny store (bez odświeżenia z API)
```

## 5. Agent przypomnień — dry-run (domyślny)

```
$ python3 -m invoicepilot --offline --store demo-state remind --as-of 2026-10-21

MODE: OFFLINE — ... | dry-run: bez wysyłki
remind: as_of=2026-10-21 dry_run=True invoices=1
  INV2-3F7F9A623B4E action=remind level=1 executed=False — zaplanowane na 2026-10-27; jeszcze nie termin
  INV2-3F7F9A623B4E action=final_notice level=2 executed=False — zaplanowane na 2026-11-03; jeszcze nie termin
log decyzji: demo-state/decisions.jsonl
```

## 6. Agent przypomnień — execute (D+7, D+14, D+21)

```
$ python3 -m invoicepilot --offline --store demo-state remind --execute --as-of 2026-10-27
  INV2-3F7F9A623B4E action=remind level=1 executed=True — przypomnienie 1 (dzień 7)
  INV2-3F7F9A623B4E action=final_notice level=2 executed=False — zaplanowane na 2026-11-03; jeszcze nie termin

$ python3 -m invoicepilot --offline --store demo-state remind --execute --as-of 2026-11-03
  INV2-3F7F9A623B4E action=final_notice level=2 executed=True — ostateczne wezwanie (dzień 14) + eskalacja do człowieka

$ python3 -m invoicepilot --offline --store demo-state remind --execute --as-of 2026-11-11
  INV2-3F7F9A623B4E action=escalate_manual level=3 executed=False — eskalacja: wymagana decyzja człowieka, brak akcji automatycznej
```

Log decyzji (`demo-state/decisions.jsonl`, fragment):

```
{"action": "remind", "day_offset": 7, "dry_run": false, "executed": true, "invoice_id": "INV2-3F7F9A623B4E", "level": 1, "offline": true, "reason": "przypomnienie 1 (dzień 7)", "ts": "2026-10-20T10:22:30+00:00"}
{"action": "final_notice", "day_offset": 14, "dry_run": false, "executed": true, "invoice_id": "INV2-3F7F9A623B4E", "level": 2, "offline": true, "reason": "ostateczne wezwanie (dzień 14) + eskalacja do człowieka", "ts": "2026-10-20T10:22:30+00:00"}
{"action": "escalate_manual", "day_offset": 14, "dry_run": false, "executed": false, "invoice_id": "INV2-3F7F9A623B4E", "level": 3, "offline": true, "reason": "eskalacja: wymagana decyzja człowieka, brak akcji automatycznej", "ts": "2026-10-20T10:22:30+00:00"}
```

## 7. Test negatywny: brak kluczy w trybie live

```
$ env -u PAYPAL_CLIENT_ID -u PAYPAL_CLIENT_SECRET python3 -m invoicepilot invoice create --text "..."
ERROR[config]: tryb live wymaga poświadczeń PayPal; brakuje: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET. Ustaw je w środowisku (sandbox: developer.paypal.com) albo uruchom z --offline (mock transport, bez sieci).
rc=3
```

Brak cichego fallbacku do mocka — to jest wymagane zachowanie.

## 8. Test negatywny: parser

```
$ python3 -m invoicepilot --offline invoice create --text "zlecenie: zrobcie cos"
ERROR[parse]: nie udało się sparsować zlecenia
  - brak nabywcy — dodaj linię 'Client: <nazwa> [<email>]'
  - brak waluty — dodaj 'Currency: EUR' albo użyj symbolu w cenie
  - brak pozycji — format: '2 x Konsultacja @ 450.00 EUR'
rc=2
```

## 9. Powtarzalność

```
rm -rf demo-state
python3 -m pytest -q
python3 -m invoicepilot --offline --store demo-state invoice create --text "<tekst z §2>"
```

Wszystkie powyższe wyniki odtwarzalne jedną komendą; stan `demo-state/` jest
generowany od zera.

## Ograniczenia tego dowodu

- **Nie wykonano żadnego realnego wywołania PayPal** (brak poświadczeń, zakaz
  sieci). Mock wiernie sprawdza nagłówki, uwierzytelnianie, idempotencję i
  przejścia stanów, ale nie jest sandboxem PayPal. Integracja live wymaga
  osobnego przebiegu z kluczami sandbox i nie jest tu udowodniona.
- Czas testów zmierzony na tym hoście (0.88 s), nie jest SLO.
