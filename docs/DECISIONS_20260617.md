# DECISIONS_20260617 - Pan Majster

## Stale decyzje

### Platnosci/faktury

* Platnosci i faktury sa na koncu.
* Nie budowac teraz Stripe/BLIK/faktur.
* MVP ma miec wartosc bez platnosci.

### Portfolio

* Portfolio jest wazne marketingowo, ale pozniej.
* Portfolio moze dzialac jako wizytowka/mini strona firmy.
* Nie rozwijac portfolio teraz.
* Jesli w kodzie/README istnieja juz zalazki portfolio, nie oznacza to, ze ten modul jest aktualnym priorytetem.

Portfolio tylko dla:

* szef firmy,
* samodzielny majster.

Portfolio NIE dla:

* inwestor,
* company_worker,
* link-only wykonawca,
* public client.

Zasady portfolio na przyszlosc:

* opt-in,
* nic nie publikuje sie automatycznie,
* wlasciciel recznie wybiera zakonczone zlecenie,
* wlasciciel recznie wybiera zdjecia,
* wlasciciel wpisuje opis,
* dane klienta sa ukrywane,
* publikacja reczna.

### Android

* Android pozniej jako PWA/Capacitor wrapper.
* Nie przepisywac aplikacji od zera na Kotlin/React Native na start.
* Najpierw web MVP.

### PDF

* PDF nie blokuje MVP.
* Pelne PDF-y / ladniejsze raporty moga byc dodatkiem pozniej.
* Nie rozwijac teraz PDF polish.

### Render

* Render nie jest zrodlem prawdy dla aktualnego local HEAD.
* Po cleanupach nie bylo pusha/deploya.
* Render/origin moze miec starsza wersje.
* Przed testem online potrzebny swiadomy push + Render smoke.

### Role

Role sa swiete i trzeba trzymac sie `ROLE_ACCESS_MATRIX.md`.

* szef firmy: `Majstrowie i ekipy`, zlecenia, linki, zarzadzanie firma,
* inwestor: `Wykonawcy`, nie `Majstrowie i ekipy`,
* samodzielny majster: wlasne zlecenia, bez panelu ludzi,
* company_worker: tylko przypisane zlecenia, bez zarzadzania wykonawcami,
* link-only `/g/...`: tylko jedno zlecenie,
* public client `/c/...`: tylko publiczny podglad.

### Nie mieszac etapow

Nie laczyc w jednym commicie:

* refaktoru,
* nowej funkcji,
* fixow,
* deploya,
* dokumentacyjnego checkpointa z kodem.

### Male kroki

Po kazdym kroku:

* git status,
* git diff,
* testy/build, jesli dotyczy,
* osobny commit,
* bez push/deploy bez decyzji.
