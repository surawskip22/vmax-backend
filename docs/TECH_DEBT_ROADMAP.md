# TECH_DEBT_ROADMAP — Pan Majster

## 1. Cel sprzątania

Ten dokument porządkuje dług techniczny po checkpointach QA i UI. Nie dodajemy tu nowych funkcji, tylko układamy projekt pod kolejne etapy pracy.

Cele sprzątania:

* wyjść z długu technicznego małymi krokami,
* przygotować projekt pod późniejsze 5E, PDF, portfolio i kolejne moduły,
* nie mieszać cleanupu z rozwojem funkcji produktu,
* commitować każdy cleanup osobno,
* utrzymać każdy krok jako mały, testowalny i odwracalny,
* nie zmieniać zachowania aplikacji bez wyraźnej decyzji.

## 2. Zasady sprzątania

Zasady dla wszystkich etapów cleanupu:

* zero dużego refaktoru naraz,
* zero 5E, PDF i portfolio podczas cleanupu,
* zero deploya na Render bez osobnej decyzji,
* backend `access.py` ma być źródłem prawdy dla uprawnień,
* frontend może ukrywać albo pokazywać UI, ale backend nadal pilnuje reguł,
* każdy etap, który dotyka kodu, kończy się testami i buildem,
* każdy etap ma osobny commit,
* bez pusha, chyba że użytkownik wyraźnie poprosi.

## 3. CLEANUP-01 — backend access jako jedno źródło prawdy

Zakres:

* znaleźć duplikacje logiki uprawnień w `api.py`, serializerach i endpointach,
* szczególnie sprawdzić `can_edit_details`,
* zastąpić hardcoded role check użyciem `ProjectAccess` / `access.py`,
* dodać małe testy regresji,
* nie dzielić jeszcze całego `api.py` na routery.

Cel:

* jedna reguła biznesowa ma być w jednym miejscu,
* uniknąć rozjazdu między `ROLE_ACCESS_MATRIX.md`, `access.py` i `api.py`,
* ograniczyć ryzyko przypadkowego przyznania uprawnień rolom link-only, public client albo company_worker.

## 4. CLEANUP-02 — test gaps backend

Zakres:

* owner/manager widzi worker links,
* company_worker, guest i public client nie widzą worker links,
* investor nie przypisuje wykonawcy spoza swojego zakresu,
* company_worker nie tworzy guest-linku,
* demo seed `--reset` bez `--yes` odmawia wykonania,
* `alembic upgrade head` działa na świeżej bazie od zera.

Cel:

* zabezpieczyć najważniejsze reguły przed kolejnymi zmianami,
* złapać regresje uprawnień przed UI albo deployem,
* mieć pewność, że demo seed i migracje zachowują się przewidywalnie.

## 5. CLEANUP-03 — frontend safety net

Zakres:

* sprawdzić, czy można dodać minimalny Vitest / React Testing Library bez dużej przebudowy,
* dodać test sidebar per rola,
* dodać test, że `ManageProjectModal` ma tylko `Dane` i `Wykonawca`,
* dodać test, że inwestor widzi `Wykonawcy`,
* dodać test, że szef widzi `Majstrowie i ekipy`,
* dodać test, że company_worker nie widzi panelu ludzi.

Cel:

* mieć minimalną ochronę przed refaktorem `App.tsx`,
* szybciej wykrywać rozjazdy między rolą użytkownika a widocznym UI,
* nie blokować projektu dużym wdrożeniem nowego toolingu.

## 6. CLEANUP-04 — frontend role labels/helpers

Zakres:

* wydzielić `roleLabels.ts`, `role.ts` albo `access.ts`,
* przenieść labelki ról i helpery z `App.tsx`,
* nie zmieniać UI,
* nie zmieniać zachowania,
* po zmianie uruchomić typecheck i build.

Cel:

* przestać powtarzać teksty i role w wielu miejscach,
* ułatwić utrzymanie języka dla szefa, inwestora, samodzielnego majstra i company_worker,
* zmniejszyć ryzyko pomylenia etykiet typu `Wykonawcy` i `Majstrowie i ekipy`.

## 7. CLEANUP-05 — AppShell / RoleAwareSidebar

Zakres:

* wydzielić shell aplikacji,
* wydzielić sidebar i nawigację,
* nie zmieniać widoków biznesowych,
* nie ruszać project detail i modali.

Cel:

* zmniejszyć `App.tsx`,
* oddzielić nawigację od logiki zleceń,
* ułatwić późniejsze testy per rola,
* nie otwierać jeszcze dużego refaktoru całego frontendu.

## 8. CLEANUP-06 — ManageProjectModal

Zakres:

* wydzielić modal do osobnego komponentu,
* zachować obecne zachowanie:

  * zakładka `Dane`,
  * zakładka `Wykonawca`,
  * link dla wykonawcy jednorazowego,
  * język szefa i inwestora,
  * company_worker bez zarządzania wykonawcą,
  * pole `Powiąż z profilem` schowane w `Zaawansowane`.

Cel:

* ograniczyć ryzyko regresji w jednym z najważniejszych flow,
* uprościć dalsze zmiany przy wykonawcach i linkach jednorazowych,
* przygotować miejsce na testy modalowe bez ruszania reszty UI.

## 9. CLEANUP-07 — backend router split

Zakres:

* wykonać dopiero po access cleanup,
* podzielić `api.py` na routery:

  * auth,
  * projects,
  * entries,
  * workers,
  * reports,
  * public,
  * admin.

* routery mają importować access helpery, a nie tworzyć własne zasady uprawnień.

Cel:

* zmniejszyć `api.py`,
* łatwiej rozwijać kolejne etapy bez przypadkowego ruszania cudzych endpointów,
* utrzymać jeden model uprawnień po stronie backendu.

## 10. CLEANUP-08 — infra stability

Zakres:

* dodać logowanie schema repair, kiedy realnie coś naprawia,
* dodać test świeżej bazy od zera,
* zaplanować późniejsze wygaszenie schema repair,
* osobna baza Pan Majster później, bliżej produkcyjnego finiszu,
* storage obiektowy później razem z migracją mediów.

Ważne:

* obecne dzielenie bazy z RCP jest tymczasowe,
* docelowo Pan Majster ma mieć normalną osobną bazę,
* nie robić cutover DB ani storage teraz w trakcie cleanupu kodu.

## 11. Czego nie robimy w cleanupie

W ramach cleanupu nie robimy:

* 5E,
* PDF layout,
* portfolio,
* płatności,
* dashboard kafelków,
* osobnej DB teraz,
* storage cutover teraz,
* pełnego PWA / offline polish,
* push notifications,
* analytics.

## 12. Rekomendowana kolejność

1. CLEANUP-01 — access.py/api.py jako jedno źródło prawdy.
2. CLEANUP-02 — backend test gaps.
3. CLEANUP-03 — minimalny frontend safety net.
4. CLEANUP-04 — role labels/helpers.
5. CLEANUP-05 — AppShell/sidebar.
6. CLEANUP-06 — ManageProjectModal.
7. CLEANUP-07 — router split.
8. CLEANUP-08 — infra stability.
9. Dopiero potem nowe funkcje: 5E/PDF/portfolio/dashboard.

## 13. Status po tym dokumencie

Ten dokument jest planem, nie implementacją.

Nie zmienia działania aplikacji, nie zmienia uprawnień i nie dodaje nowych funkcji.

Każdy kolejny cleanup wymaga osobnego promptu, osobnej weryfikacji i osobnego commita.
