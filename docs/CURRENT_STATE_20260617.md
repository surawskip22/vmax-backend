# CURRENT_STATE_20260617 - Pan Majster

## Stan repo

* branch: `pan-majster`
* HEAD przed tym checkpointem: `61bcaac docs: add local smoke check after cleanup`
* status przed tym checkpointem: czysty, potwierdzony komenda `git status --short`
* 5E: nie rozpoczete

## Ostatni znany zielony stan

* alembic upgrade head: OK
* pytest: OK, 24 passed
* frontend source-regression: OK
* tsc -b: OK
* vite build: OK
* lokalny smoke webowki: OK

## Lokalny smoke

* backend `/api/health`: OK
* UI `/app`: OK
* brak bialej strony
* brak `Failed to fetch`
* szef firmy: OK
* inwestor: OK
* samodzielny majster: OK
* `pracownik@majster.pl`: OK
* `pracownik2@majster.pl`: OK
* public client `/c/...`: OK
* link-only `/g/...`: nie byl sprawdzany, bo surowy token nie byl dostepny bez wygenerowania nowego linku

## Konta testowe / demo

* Szef firmy:
  * `szef@majster.pl / test1234`
* Inwestor:
  * `inwestor@majster.pl / test1234`
* Samodzielny majster:
  * `samodzielny@majster.pl / test1234`
* Company worker / majster firmy:
  * `pracownik@majster.pl / test1234`
* Company worker / drugi majster firmy, jesli istnieje:
  * `pracownik2@majster.pl / test1234`

Uwaga:

* wykonawcy link-only nie loguja sie e-mailem,
* link-only dziala przez `/g/...`,
* klient publiczny dziala przez `/c/...`,
* inwestor ma widziec `Wykonawcy`, nie `Majstrowie i ekipy`.

## Render / origin

* po cleanupach nie bylo pusha,
* po cleanupach nie bylo Render deploy,
* Render/origin moze miec starsza wersje niz lokalny HEAD,
* nie oceniac aktualnego local HEAD po Renderze bez swiadomego pusha i deploy smoke.

## Ostatnie wazne prace

* UX fix modala `Edytuj zlecenie -> Wykonawca`,
* zakladki `Dane` / `Wykonawca`,
* link jednorazowy dla wykonawcy przeniesiony do zakladki `Wykonawca`,
* copy wyjasniajace, ze link dziala tylko do jednego zlecenia, nie tworzy konta i e-mail jest opcjonalny,
* `Powiaz z profilem` schowane w `Zaawansowane`,
* QA audit,
* tech debt roadmap,
* backend access cleanup przez `ProjectAccess`,
* backend regression guards,
* frontend source-regression,
* frontend `access.ts` / `roleLabels.ts`,
* `AppShell` / `RoleAwareSidebar`,
* `ManageProjectModal`,
* local smoke check.

## Aktualna decyzja

* cleanup zatrzymujemy na tym etapie,
* nie tniemy dalej `App.tsx` / `api.py` bez konkretnego powodu,
* nie zaczynamy nowych funkcji,
* kolejny sensowny krok to test zewnetrzny albo swiadomy push + Render smoke,
* fixy tylko przy blockerach.
