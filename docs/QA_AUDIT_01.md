# QA-AUDIT-01 - Pan Majster

## 1. Stan repo

- Branch: `pan-majster`
- HEAD: `0013b74 feat: finish project detail flow and add demo data`
- Working tree: nieczysty, ale zmiany sa ograniczone do frontendu:
  - `frontend/src/App.tsx`
  - `frontend/src/styles.css`
- Obecny diff: `2 files changed, 141 insertions(+), 109 deletions(-)`
- Audyt obejmuje stan roboczy po `PROJECT-DETAIL-UX-FIX-02B`, nie czysty checkpoint.
- `alembic upgrade head`: OK
- `pytest`: OK, `20 passed`
- `tsc -b`: OK
- `vite build`: OK

## 2. Ogolna ocena

- Gotowe do testow zewnetrznych: warunkowo tak, po swiadomym domknieciu i commicie obecnego UX fixa modala wykonawcy/linku.
- Najwieksze ryzyka:
  - `frontend/src/App.tsx` ma ok. 3926 linii i laczy routing, role, widoki, modale, raporty, ludzi, publiczne strony i offline queue.
  - `panmajster/api.py` ma ok. 2629 linii i nadal miesci wiele endpointow oraz czesc decyzji produktowo-uprawnieniowych.
  - Frontend nie ma testow komponentowych/e2e, wiec regresje UI sa wykrywane glownie przez manual i typecheck.
  - Runtime schema repair jest praktyczne dla stagingu, ale jako mechanizm produkcyjny powinien miec plan wygaszenia.
- Rekomendacja: nie zaczynac duzego 5E ani PDF/portfolio przed malym porzadkowaniem struktury i jednym zewnetrznym smoke testem.

## 3. Blockery przed kolejnym etapem

- Brak twardych blockerow technicznych: migracje, backend tests, typecheck i build przechodza.
- Przed kolejnym etapem trzeba rozstrzygnac stan roboczy:
  - albo zrobic manual smoke obecnego modala wykonawcy/linku i commit,
  - albo swiadomie zostawic te zmiany jako otwarty UX fix.
- Nie rekomenduje dalszego doklejania UI do `App.tsx` bez planu wydzielania komponentow.

## 4. Rzeczy do poprawy przed testem zewnetrznym

- Zrobic krotki smoke test ról:
  - szef: zlecenia, majstrowie/ekipy, modal wykonawcy/linku, raporty,
  - inwestor: inwestycje, wykonawcy, modal wykonawcy/linku,
  - company_worker: brak panelu ludzi, readonly terminy/kwota,
  - link-only: tylko przypisane zlecenie i dozwolone akcje.
- Sprawdzic na Renderze, czy demo seed faktycznie zostal wykonany na staging DB.
- Upewnic sie, ze service worker nie trzyma starego UI po deployu.
- Dopracowac copy w miejscach, gdzie role moga sie mylic: wykonawca vs majster/ekipa vs link jednorazowy.

## 5. Dlug techniczny

### Frontend

- `App.tsx` jest glownym ryzykiem utrzymaniowym. Warto wydzielac bez zmiany zachowania.
- `styles.css` ma ok. 952 linie i miesza globalne style, layout, modale, listy, raporty i mobile.
- Role-aware labelki sa czytelne, ale rozproszone po helperach i komponentach.
- `ReportsPage`, `ProjectsPage`, `ProjectView`, `ManageProjectModal`, `CompanyTeamPanel` i `InvestorContractorsPanel` maja juz dosc zlozona logike lokalna.
- Search/sort/filter sa frontowe. To jest OK dla demo, ale powinno byc jasno traktowane jako filtr po aktualnie pobranej liscie, nie pelne filtrowanie backendowe.

### Backend

- `access.py` centralizuje wazna czesc uprawnien, co jest dobre.
- `api.py` nadal zawiera duzo reguł szczegolowych: final status, stage, workers, guest links, public payloads, reports.
- Endpointy sa nazwane sensownie, ale plik powinien pozniej zostac podzielony na routery: auth, projects, entries, workers, reports, public, admin.
- Fallback `POST /api/projects/{project_id}/stages/{stage_id}` obok `/set-current` jest dlugiem kompatybilnosci po starym UI/405. Warto go oznaczyc do usuniecia po stabilizacji frontendu.
- Runtime schema repair w `db.py` jest uzasadniony po driftach Render/staging, ale przed produkcja warto ograniczyc go do jawnego trybu naprawczego albo bardzo waskiej listy kolumn.

### Testy

- Testy backendowe sa mocne jak na etap prototypu: role, link-only, public client, close/reopen, stage, contract terms, demo seed i schema repair sa pokryte.
- Brakuje testow frontendowych. Typecheck/build lapia typy, ale nie lapia mylnych labeli, zlych sekcji, przyciskow bez handlera ani regresji layoutu.
- Czesc testow frontendowych jest realizowana przez statyczne asercje w `tests/test_flow.py`, co jest dobre awaryjnie, ale kruche przy refaktorze.

### Deploy

- Docker start command jest poprawny: migracje, potem `uvicorn panmajster.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}`.
- Render uzywa branch `pan-majster`.
- `healthCheckPath: /api/health` jest ustawiony.
- `/api/version` istnieje i pomaga w diagnozie deployow.
- Static frontend jest kopiowany z build stage do `/app/static` i serwowany przez FastAPI.
- `index.html` i `sw.js` maja `Cache-Control: no-cache`, co ogranicza ryzyko starego frontendu.

### Dane/demo

- `demo_seed.py` wymaga `--reset --yes`.
- W production reset wymaga `PANMAJSTER_ALLOW_DEMO_RESET=1`.
- Nie ma publicznego endpointu resetujacego.
- Demo seed nie odpala sie automatycznie przy starcie.
- Lokalny `init_db()` nadal seediuje podstawowe konta dev, gdy `APP_ENV != production`, co jest OK dla dev, ale Render ma `APP_ENV=development`; trzeba pamietac, ze to nie jest produkcyjna konfiguracja.

## 6. Sugestie refaktoru

Najpierw male i bezpieczne:

1. Wydzielic `frontend/src/role.ts` albo `frontend/src/roleLabels.ts`.
   - Po co: jeden slownik dla labeli ról i menu.
   - Kiedy: przed kolejnymi zmianami ról/ludzi.
2. Wydzielic `AppShell` i `RoleAwareSidebar`.
   - Po co: odseparowac nawigacje od widokow.
   - Kiedy: przed kolejnym UI etapem.
3. Wydzielic `ProjectsPage`, `ProjectView`, `ReportsPage`.
   - Po co: najwieksze powierzchnie regresji UI.
   - Kiedy: partiami, po commicie obecnego UI.
4. Wydzielic `ManageProjectModal`.
   - Po co: aktualnie laczy dane, status, wykonawce, link jednorazowy, terminy i role.
   - Kiedy: bez nowych funkcji, jako czysty refactor z testem build.
5. Backend: podzielic `api.py` na routery.
   - Po co: zmniejszyc ryzyko przypadkowego dotkniecia cudzych endpointow.
   - Kiedy: po zewnetrznym smoke albo przed duzym PDF/5E.

## 7. Ryzyka bezpieczenstwa/danych

- Link-only:
  - ograniczenia sa testowane; link-only nie powinien widziec pelnej aplikacji.
  - nalezy nadal pilnowac, zeby frontend nie pokazywal zarzadczych akcji przy guest tokenie.
- Public client:
  - payload public project usuwa `client_email`.
  - public client nie ma edycji i testy to sprawdzaja.
- Demo reset:
  - brak publicznego endpointu resetujacego.
  - reset produkcyjny wymaga dodatkowego env.
  - ryzyko operacyjne: reczne odpalenie zlym DATABASE_URL. Procedura powinna zawsze potwierdzac srodowisko bez wypisywania sekretow.
- Sekrety:
  - nie stwierdzono oczywistego tracked `.env`.
  - `.env.example` zawiera placeholdery i testowe wartosci.
  - nie wypisano zadnych sekretow.
- Role:
  - centralizacja w `access.py` pomaga.
  - frontend nadal ma niezalezna logike ukrywania akcji, wiec backend musi pozostac zrodlem prawdy.

## 8. Test gaps

- Dodac test regresji dla payloadu project detail:
  - owner/manager widzi worker links,
  - company_worker/guest/public nie widza worker links.
- Dodac test dla starego fallback endpointu stage bez `/set-current` albo zaplanowac jego usuniecie.
- Dodac test, ze investor nie moze przypisac worker profile spoza swojego workspace/listy.
- Dodac test, ze company_worker nie moze tworzyc worker guest-link.
- Dodac test demo seed CLI guard: `--reset` bez `--yes` odmawia.
- Frontend minimum:
  - smoke test renderu nawigacji per rola,
  - ManageProjectModal pokazuje tylko `Dane` i `Wykonawca`,
  - investor ma `Wykonawcy`, szef ma `Majstrowie i ekipy`,
  - company_worker nie widzi panelu ludzi.

## 9. Rekomendowana kolejnosc kolejnych krokow

1. Domknac obecny UX fix modala wykonawcy/linku: manual smoke, ewentualnie commit.
2. Zrobic zewnetrzny smoke z jedna osoba na Render demo.
3. Naprawic tylko feedback blokujacy z testu zewnetrznego.
4. Zrobic maly refactor frontendu bez zmiany zachowania: role labels, sidebar, ManageProjectModal.
5. Dopiero potem decyzja: 5E/listy/sortowanie albo finalne role wykonawcy, zależnie od feedbacku.
6. PDF/raporty po stabilizacji podstawowego flow.
7. Portfolio, platnosci, PWA, powiadomienia jako pozniejsze etapy.

## 10. Co zostawic na pozniej

- Platnosci/faktury.
- Publiczne portfolio jako pelny produkt.
- Pelny sklad ekip i zaawansowana dostepnosc.
- PWA offline polish poza obecnym minimum.
- Push notifications.
- Analytics.
- Backendowe filtrowanie/sortowanie list, dopoki demo skala jest mala.

## Koncowe wnioski

- Kod nie byl zmieniany podczas audytu; utworzono tylko ten raport.
- Commit: nie.
- Push: nie.
- 5E: nie.
- Rekomendacja: isc do testow zewnetrznych dopiero po manualnym smoke i commicie obecnego UX fixa. Refactor robic malymi partiami po smoke, nie jako duzy jednorazowy remont.
