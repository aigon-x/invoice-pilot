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

## Warstwa AI i webhook (2026-10-20)
- `invoicepilot/ai.py`: ekstrakcja zlecenia przez LLM (OpenAI-compatible, env `PAYPAL_AI_BASE_URL`,
  `PAYPAL_AI_API_KEY`, `PAYPAL_AI_MODEL`); bez klucza deterministyczny ekstraktor regułowy.
  Tryb jest zawsze jawny w wyjściu (`ai: LLM` albo `ai: RULE-BASED`).
- `invoicepilot/webhook.py` + `PayPalClient.verify_webhook_signature`: weryfikacja przez
  `/v1/notifications/verify-webhook-signature`; offline akceptuje wyłącznie
  `paypal-transmission-sig: offline-valid`. Zdarzenie `INVOICING.INVOICE.PAID` zmienia status.

```
$ python3 -m pytest -q
44 passed in 0.93s
```

```
$ python3 -m invoicepilot --offline --store /tmp/ppstate2 webhook --file /tmp/wb.json --headers /tmp/wbh.json
{"verified": true, "event_type": "INVOICING.INVOICE.PAID", "invoice_id": "INV2-AEA10AF3D827", "status": "PAID", "applied": true}
```

Sandbox PayPal nie był wywołany — brak `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`.

## Warstwa webowa — UI/UX (2026-10-20)

`web/server.py` (FastAPI) + `web/index.html` (jeden plik, zero zależności JS)
+ `tests/test_web.py`. Przeglądarka nie rozmawia z PayPal: każdy request idzie
przez ten sam deterministyczny mock co `--offline`, a stan trafia do
`state/web/` (osobno od CLI). Klient PayPal i parser pozostały nietknięte.

Endpointy:

| Metoda | Ścieżka | Zachowanie |
|---|---|---|
| GET | `/` | `web/index.html` — ciemny interfejs, akcent `#4c82d9` |
| POST | `/api/invoice` | `{"text": "..."}` → parsowanie, mock PayPal, status `SENT`; zwraca `id`, `number`, `total`, `currency`, `status`, `items`, `ai_mode` |
| POST | `/api/webhook` | symuluje `INVOICING.INVOICE.PAID` z nagłówkiem `offline-valid`; zwraca nowy status |
| GET | `/api/health` | `status`, `mode=offline`, liczba faktur w store |

Testy (cały projekt):

```
$ python3 -m pytest -q -p no:warnings
51 passed in 2.71s
```

Siedem testów web: index 200 + treść UI, health, invoice → `SENT`
(total `2790.00 EUR`, 2 pozycje, `RULE-BASED`), webhook → `PAID`,
webhook bez id → najnowsza faktura, zły tekst → 400 z listą problemów
parsera i bez zapisu do store, nieznana faktura w webhooku → 404.

Smoke na żywo (uvicorn `127.0.0.1:8017`, stan w katalogu tymczasowym):

```
GET  /              -> 200, 18 727 B
GET  /api/health    -> {"status":"ok","mode":"offline","version":"1.0.0","invoices":0,...}
POST /api/invoice   -> id=INV2-9F6F8571E594 number=INV-20261020-2954 total=2790.00
                       currency=EUR status=SENT items=2 ai_mode=RULE-BASED
POST /api/webhook   -> {"id":"INV2-9F6F8571E594","status":"PAID","verified":true,"applied":true}
POST /api/invoice   -> 400 {"detail":{"error":"order text could not be parsed","problems":[...]}}
```

UI pokazuje: textarea na zlecenie, „Create invoice”, stan pusty („No invoice
yet”), stan ładowania (spinner + komunikat w `aria-live`), stan błędu
(`role="alert"`, lista problemów), kartę faktury (numer, kwota, status pill),
tabelę pozycji oraz „Simulate payment”, który po zdarzeniu zmienia pill na
`PAID` i blokuje przycisk. Dodatkowo: `aria-live="polite"`, focus przenoszony
na błąd, `prefers-reduced-motion`, responsywność (375 px zweryfikowane), favicon inline
(data URI), obsługa Ctrl/⌘+Enter.

Uruchomienie: `python3 -m uvicorn web.server:app --port 8017`
(wymaga `fastapi` i `uvicorn`; testy wymagają `httpx`).

Render zweryfikowany headless Chromium (Playwright, viewport 1280×900 i 375×812,
tylko `127.0.0.1`, zero egressu): stan pusty widoczny przed akcją, po
„Create invoice” karta pokazuje `SENT`, 2 wiersze i kwotę `2790.00 EUR`;
„Simulate payment” zmienia pill na `PAID` i blokuje przycisk; zły tekst
pokazuje `role="alert"` z trzema problemami parsera i ukrywa kartę faktury.
`page_errors` = 0; jedyny wpis w konsoli to oczekiwane 400 z testu
negatywnego. Widok 375 px nie ma poziomego przewijania (kolumna „Unit”
ukryta na telefonie). Bramka MCP `browser-render` była niedostępna
(`TOR_DOWN`), więc render wykonano lokalnie bez proxy — bez żadnego
połączenia zewnętrznego.

Ograniczenia: brak autoryzacji i wielodostępności (demo), stan lokalny
(JSON), brak SSE/websocketów — status zmienia odpowiedź HTTP, webhook jest
symulowany z jawnym nagłówkiem `offline-valid`, a nie podpisany przez PayPal.

## PayPal sandbox — przebieg live (2026-10-20)
Poświadczenia sandboxa w Vaulcie (`secret/monai/paypal`) i w env MONAI (600).
```
oauth: 200
invoice rejected: 403 {"name":"NOT_AUTHORIZED","message":"Authorization failed due to insufficient permissions."}
```
Wniosek: klucze są poprawne (OAuth przechodzi), ale aplikacja nie ma włączonej funkcji
Invoicing. Po włączeniu funkcji w panelu aplikacji: `python3 scripts/paypal_smoke.py` → oczekiwany 0.

## Live sandbox po włączeniu Invoicing (2026-10-20)
```
oauth: 200
POST /v2/invoicing/invoices -> 201
Location: https://api-m.sandbox.paypal.com:8443/v2/invoicing/invoices/INV2-UGKV-2FPL-TRG5-UAZ3
Paypal-Debug-Id: ca4d95a979ed5
```
Faktura utworzona w sandboxie na prawdziwym API PayPal. Klucze: `secret/monai/paypal`.
