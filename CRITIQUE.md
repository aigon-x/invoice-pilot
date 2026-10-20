# CRITIQUE — InvoicePilot (sceptyk, 2026-10-20)

**Werdykt:** solidny, uczciwie opisany CLI, ale to nie jest zgłoszenie na hackathon AI: brak AI, brak realnego PayPala, brak płatności i brak wideo.

**Pomiar:** `pytest -q` → 37 passed (0.44 s); `run_demo.sh` odtwarza EVIDENCE co do ID, kwoty i request-id; GitHub API: repo publiczne, `private=false`, licencja MIT.

## Oceny (0–100)
| Oś | Ocena | Dowód |
|---|---:|---|
| Technical execution | 65 | 2 022 linie, czyste moduły, retry/idempotencja/timeouty; live path nieprzetestowany, mock nie waliduje kontraktu (`transport.py:161-176`). |
| Innovation | 30 | Regex parser (`parser.py:17-37`) + harmonogram 0/7/14; kategoria crowded, zero AI. |
| PayPal API | 45 | Poprawnie wyglądający OAuth2 + Invoicing v2 (`paypal.py:144-250`), ale zero wywołań sandbox (`EVIDENCE.md:153-156`), brak płatności/webhooków. |
| Evidence quality | 42 | Odtwarzalne, uczciwe ograniczenia; dowody są samospójne z mockiem (`tests/test_paypal_client.py:50-64`), brak śladów HTTP/ID z sandboxa, brak wideo. |
| Requirements compliance | 30 | Repo publiczne + MIT + instrukcje = PASS; brak wideo (`DEVPOST_DRAFT.md:87`), brak AI, brak realnego przepływu płatności. |
| Ryzyko odrzucenia | 78 | Patrz sekcja „Ryzyko” (wyżej = gorzej). |

**Średnia 5 osi jakości: 42/100.** Ryzyko odrzucenia traktuję osobno: **78/100**.

## Co jest realne (wycofuję potencjalne fałszywe alarmy)
- Testy przechodzą (37), demo odtwarza EVIDENCE 1:1, repo jest publiczne i MIT — to sprawdziłem, nie jest deklaracją.
- `send` z `{"send_to_invoicer": true}` jest zgodny ze schematem PayPal (requestBody `notification`), więc to NIE jest błąd.
- Brak kluczy w live kończy się `rc=3` i czytelnym komunikatem; nie ma cichego fallbacku do mocka (`paypal.py:59-78`, `tests/test_cli.py:70-77`).

## Słabości (dowody)
1. **Zero AI.** Cały „agent” to deterministyczny scheduler; parser to regex (`parser.py:17-37`). W kodzie nie ma żadnego LLM/inference; LLM jest dopiero w „What's next” (`DEVPOST_DRAFT.md:78-79`). Regulamin definiuje Projekt jako PayPal **+ AI tool/model** → ryzyko niezgodności kwalifikacyjnej.
2. **Zero realnego PayPala.** EVIDENCE sam przyznaje: „Nie wykonano żadnego realnego wywołania PayPal” (`EVIDENCE.md:153-156`); „Real sandbox verification” jest w planach (`DEVPOST_DRAFT.md:74`). Sędzia widzi `MODE: OFFLINE` i mock, nie integrację.
3. **Brak przepływu płatności.** Kod robi create/send/get/remind; nie ma payer checkout, `/payments`, webhooków, a PAID widać tylko po ręcznym `--refresh` (`README.md:147-148`, `cli.py:170-181`, `reminders.py:15`). Wymóg „realny przepływ płatności” nie jest spełniony.
4. **Brak wideo (mandatory).** `DEVPOST_DRAFT.md:87` ma placeholder `<VIDEO_URL>`; checklista: „Wideo (<3 min) | BRAK” (`SUBMISSION-CHECKLIST.md:15`). Bez wideo zgłoszenie nie spełnia Submission Requirements.
5. **Mock overclaims.** README mówi, że mock waliduje „shape” (`README.md:145`), a kod sprawdza tylko JSON i `PayPal-Request-Id`, nie sprawdza nawet Bearer na create i sumuje kwoty `float` (`transport.py:161-176`, `transport.py:173`). Testy dowodzą zgodności z mockiem, nie z PayPalem.
6. **Live state: 202 ≠ SENT.** Przy przyszłej `invoice_date` PayPal zwraca 202 i dopiero planuje wysyłkę, a CLI i tak ustawia `status=SENT` i `sent_on=today` (`cli.py:126-131`, `paypal.py:205-218`) → przypomnienia D+7/D+14 liczą się od złej daty.
7. **JPY złamie live.** `format_amount` zawsze daje 2 miejsca (`models.py:25`), a PayPal dla JPY wymaga integer; README to przyznaje (`README.md:150-151`), ale nie ma testu ani walidacji.
8. **Checklista przeczy rzeczywistości.** „Repo publiczne — BLOKADA” (`SUBMISSION-CHECKLIST.md:13`), a GitHub API potwierdza `private=false`. Drift osłabia wiarygodność pozostałych dowodów.
9. **Język.** Materiały mają być po angielsku; CLI/errors są po polsku (`cli.py:140-148`, `paypal.py:70-77`) — potrzebne tłumaczenie wideo/instrukcji.

## 5 ulepszeń po wpływie
| # | Ulepszenie | Wpływ | Dowód / zakres |
|---|---|---:|---|
| 1 | Nagrać <3 min wideo: live sandbox, create→send→payer płaci→`status --refresh` = PAID; pokazać `MODE: LIVE`. | 10/10 | `DEVPOST_DRAFT.md:87`, `SUBMISSION-CHECKLIST.md:15`; bez tego brak zgłoszenia. |
| 2 | Dodać AI: LLM extraction brudnego zlecenia + walidacja schematu (fallback na regex); pokazać w demo. | 9/10 | `parser.py:17-37`, `DEVPOST_DRAFT.md:78-79`; domyka definicję Projektu i kryterium „PayPal + AI”. |
| 3 | Dodać `scripts/smoke_live.sh` + trace: request-id, `Paypal-Debug-Id`, HTTP status, invoice id do `state/api_trace.jsonl`. | 8/10 | `paypal.py:89-121`, `transport.py:55-64`; zamienia „tak wygląda” na odtwarzalny dowód HTTP. |
| 4 | Naprawić live semantics: 202/scheduled ≠ SENT, `sent_on` z realnego send; JPY integer; walidacja payloadu schematem PayPal. | 7/10 | `cli.py:126-131`, `paypal.py:205-218`, `models.py:25`. |
| 5 | Uporządkować dowody: checklista zgodna z rzeczywistością, EVIDENCE z żywym runem, README bez „offline by default” w opisie repo. | 6/10 | `SUBMISSION-CHECKLIST.md:13`, `EVIDENCE.md:153-156`. |

## Co zrobić, by sędzia uwierzył w prawdziwy PayPal
- Wideo jednym ujęciem: terminal `MODE: LIVE — https://api-m.sandbox.paypal.com`, create, send, potem dashboard sandbox z tym samym `INV2-...` i statusem SENT.
- Druga scena: sandboxowy payer płaci fakturę; `invoice status <id> --refresh` pokazuje PAID, a `remind` nie wysyła nic (`reminders.py:15,79`).
- Commit `docs/SANDBOX-RUN.md`: daty, request-id, `Paypal-Debug-Id`, HTTP status, Location/ID (bez sekretów). To jest dowód, którego dziś nie ma.
- Dostarczyć `scripts/smoke_live.sh`, który sędzia uruchomi na WŁASNYCH kluczach sandbox i dostanie 0/3/4/5 zamiast deklaracji.
- Pokazać także ścieżkę negatywną na żywo: zły klucz → 401/exit 4, zły parser → exit 2; dowód, że kod naprawdę gada z API.
- Nie używać `--offline` w wideo; co najwyżej jako osobny klip „testy bez sieci”.

## Ryzyko odrzucenia — dlaczego 78/100
- **Kwalifikacja:** brak AI tool/model łamie definicję Projektu z §4; to nie jest „słabszy wynik”, to możliwa niezgodność.
- **Kompletność:** wideo jest obowiązkowe (§4), a go nie ma; Devpost może odrzucić formalnie.
- **Demonstracja:** offline mock bez płatności wygląda jak „non-functional demo” w rozumieniu §4, nawet jeśli CLI działa.

## Najlepsze pojedyncze ulepszenie
Live sandbox end-to-end z realną płatnością, nagrany jako wideo i utrwalony trace’em: jednocześnie domyka wideo, dowód integracji i „realny przepływ płatności”.

*Plik zapisany przez sceptyka; kodu nie zmieniałem. Pomiar: 2026-10-20, host local-aigon.*
