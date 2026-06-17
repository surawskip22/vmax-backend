# SMOKE_CHECK_20260617 - Pan Majster

## Stan

* branch: `pan-majster`
* HEAD przed smoke: `e5d7933 test: update frontend source assertions after refactor`
* data: `2026-06-17`
* backend URL: `http://127.0.0.1:8000`
* UI URL: `http://127.0.0.1:8000/app`

## Wyniki techniczne

* health: `{"status":"ok","service":"pan-majster","storage":"local_disk"}`
* alembic upgrade head: OK
* pytest: OK, `24 passed`
* source-regression: OK
* tsc -b: OK
* vite build: OK

## Role

| Rola | Konto | Logowanie | Menu | Zlecenia | Szczegol | Modal | Wynik | Uwagi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Szef firmy | `szef@majster.pl` | OK | OK - widzi `Majstrowie i ekipy` | OK | OK | OK - tylko `Dane` i `Wykonawca`, link jednorazowy w `Wykonawca` | OK | Modal ma jezyk `Majster / ekipa` i `Przypisz majstra / ekipe`. |
| Inwestor | `inwestor@majster.pl` | OK | OK - widzi `Wykonawcy`, nie widzi firmowego `Majstrowie i ekipy` | OK | OK | OK - tylko `Dane` i `Wykonawca`, link jednorazowy w `Wykonawca` | OK | Modal ma jezyk `Wykonawca` i `Przypisz wykonawce`. |
| Samodzielny majster | `samodzielny@majster.pl` | OK | OK - bez panelu ludzi | OK | OK | Nie dotyczy | OK | Dodawania postepu nie klikano, bo otwarty projekt testowy byl zakonczony. |
| Company worker | `pracownik@majster.pl` | OK | OK - bez panelu ludzi i bez zarzadzania wykonawcami | OK - tylko przypisane zlecenia | OK | Nie dotyczy | OK | Logowanie sprawdzone przez lokalny przycisk demo dla tego samego konta z powodu niestabilnosci automatyzacji formularza w przegladarce. |
| Company worker | `pracownik2@majster.pl` | OK | OK - bez panelu ludzi i bez zarzadzania wykonawcami | OK - tylko przypisane zlecenia | OK | Nie dotyczy | OK | Konto opcjonalne istnieje w lokalnych danych demo i zostalo sprawdzone. |

## Link-only/public client

* `/c/...` klienta: sprawdzono, OK.
* Wynik `/c/...`: publiczny podglad dziala, nie pokazuje panelu aplikacji, nie pokazuje sidebaru ani przycisku wylogowania.
* `/g/...` wykonawcy link-only: nie sprawdzono - lokalna baza przechowuje hash tokenu, a surowy link nie byl dostepny bez wygenerowania nowego linku.
* Nie generowano nowego linku `/g/...`, zeby nie zmieniac danych lokalnych podczas smoke.

## Bledy

* Brak krytycznych bledow w smoke.
* Brak bialej strony.
* Brak `Failed to fetch`.
* Brak bledow konsoli przegladarki w koncowym odczycie.
* Nie zaobserwowano, zeby role widzialy cudze panele.

## Decyzja

* gotowe do testu zewnetrznego.

## Uwaga

Ten smoke byl lokalny. Render nie byl wdrazany po aktualnym HEAD w ramach tego kroku.
