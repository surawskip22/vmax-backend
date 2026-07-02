# COMPANY_OWNER_UI_AUDIT_20260703

## Cel

Krótki checkpoint aktualnego flow `company_owner` / `Szef firmy` po serii zmian UI dla innych ról. Ten dokument nie zmienia kodu i nie jest planem dużego redesignu. Ma pokazać, czy obecny szef firmy jest bezpiecznie odseparowany od inwestora, samodzielnego majstra, `company_worker`, `/c/...` i `/g/...`.

## HEAD i zakres

Audyt wykonany na gałęzi `pan-majster`, po commicie:

`78502b6 docs: audit guest link flow`

Zakres sprawdzony statycznie:

- `frontend/src/RoleAwareSidebar.tsx`
- `frontend/src/access.ts`
- `frontend/src/roleLabels.ts`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `panmajster/access.py`
- `panmajster/api.py`
- `tests/test_flow.py`

## Nawigacja szefa firmy

`company_owner` ma w sidebarze:

- `Zlecenia`
- `Majstrowie i ekipy`
- `Raporty`
- `Ustawienia`

Nie dostaje:

- `Wykonawcy` jako języka inwestora,
- `Wyszukaj wykonawcę`,
- `Ogłoś zlecenie`,
- `Moja wizytówka`,
- workerowego `Moje zlecenia`,
- pełnej aplikacji link-only.

To jest zgodne z obecnym modelem ról: szef firmy zarządza firmą, ekipami i zleceniami firmy.

## Zlecenia firmy

Lista zleceń dla `company_owner` idzie przez wspólny `ProjectsPage`, ale ma osobne flagi:

- `companyOwnerMode`
- `ownerSimpleMode`
- klasy `company-owner-projects-page`, `company-owner-list-panel`, `project-controls--owner`, `project-list-card--owner`

Copy jest firmowe:

- nagłówek `Zlecenia`,
- opis `Zarządzaj zleceniami firmy, ekipami i postępem prac.`,
- akcja `Dodaj zlecenie`,
- wyszukiwarka po zleceniu, kliencie, majstrze, ekipie lub adresie.

Szef firmy ma dostęp do trybu `Prosty/Rozbudowany`, tworzenia zlecenia i filtrów/sortowania w wariancie rozbudowanym. Nie widać w tym miejscu bezpośredniego ryzyka pomylenia go z inwestorem lub pracownikiem firmy.

## Majstrowie i ekipy

Sekcja `Majstrowie i ekipy` dla `company_owner` przechodzi do `CompanyTeamPanel`.

Zachowanie:

- workspace firmy jest wymagany,
- szef widzi panel zespołu firmy,
- może zarządzać majstrami, ekipami, dostępami i przypisanymi zleceniami,
- inwestor idzie osobną ścieżką `InvestorContractorsPanel`,
- `company_worker` i samodzielny majster nie mają panelu ludzi w nawigacji.

Backend wspiera ten podział przez `can_manage_people()`, `can_manage_workspace()` i role workspace `owner/admin`.

## Raporty

Raporty szefa firmy używają wspólnego widoku raportów z flagą:

- `companyOwnerReports`
- klasa `independent-reports-page--company-owner`

Copy jest firmowe:

- `Przeglądaj raporty, wpisy i materiały ze zleceń firmy.`,
- `Wpisy i raporty zleceń firmy`,
- `zleceń otwartych`,
- `zleceń zakończonych`.

Widok nie jest osobnym modułem backendowym, ale frontendowo ma osobny język i klasy. To jest akceptowalne dla MVP, choć warto pilnować regresji, bo raporty są mocno współdzielone między `independent_contractor`, `investor` i `company_owner`.

## Ustawienia

`company_owner` ma osobną gałąź ustawień:

- `company-owner-settings-page`
- `company-owner-settings-stack`
- `company-owner-settings-card`

Sekcje:

- `Moje konto`
- `Dane firmy`
- `Zespół i dostępy`
- `Linki i widoczność`
- `Konto`

Widoczny dług techniczny: komponent i klasy bazowe nadal mają nazwy `worker-settings-*`. To nie jest błąd funkcjonalny, ale może mylić przy kolejnych zmianach. Nie wymaga natychmiastowego refaktoru przed testem zewnętrznym.

## Backend i uprawnienia

Backendowe reguły szefa firmy są obecnie spójne z matrixem ról:

- `company_owner` może tworzyć workspace firmy w onboardingu,
- workspace ma członkostwo `owner`,
- `can_manage_people()` pozwala zarządzać ludźmi szefowi/inwestorowi, ale blokuje `company_worker` i samodzielnego majstra,
- `can_manage_workspace()` ogranicza operacje workspace do `owner/admin`,
- tworzenie zlecenia sprawdza `can_create_project()` i dostęp do workspace,
- przypisywanie wykonawców/majstrów sprawdza workspace i prawa zarządzania.

Testy regresji w `tests/test_flow.py` obejmują m.in. onboarding szefa, workspace, worker profiles, linki majstra, close/reopen, etapy, terminy, PDF-y, public client i izolację cudzej firmy.

## Ryzyka

1. `frontend/src/App.tsx` nadal jest duży i łączy kilka flow ról w jednym pliku. Każda większa zmiana UI szefa może przypadkiem dotknąć inwestora lub samodzielnego majstra.

2. Widoki raportów są współdzielone między kilkoma rolami. To oszczędza kod, ale podnosi ryzyko regresji języka i uprawnień w UI.

3. Ustawienia szefa używają bazowych klas `worker-settings-*`. To nie psuje działania, ale jest nazwowym długiem po refaktorach UI.

4. W statycznym odczycie widać stare mojibake w kilku tekstach frontendowych w nawigacji innych ról. To powinno być osobnym małym fixem tekstowym, nie częścią tego audytu.

5. Ten audyt był statyczny. Przed zewnętrznym testem warto zrobić krótki smoke ręczny szefa firmy: lista zleceń, utworzenie zlecenia, przypisanie majstra/ekipy, link klienta, raporty, settings.

## Rekomendacja

Nie robić teraz dużego redesignu szefa firmy. Obecny układ jest wystarczająco odseparowany, żeby przejść do krótkiego smoke/testu zewnętrznego albo do jednego małego polish-fixa, jeśli użytkownik pokaże konkretny screenshot.

Najbezpieczniejsze następne kroki:

1. Mały fix tekstów mojibake w frontendzie, jeśli są widoczne w UI.
2. Krótki smoke `company_owner` bez zmian w kodzie.
3. Dopiero potem ewentualny wąski polish szefa firmy albo audyt `/g/...` decyzji produktowej o zmianie etapów.

Nie rekomenduję teraz:

- rozbijania całego `App.tsx`,
- router split `api.py`,
- przebudowy panelu ludzi,
- dodawania nowych funkcji marketplace/portfolio/audio,
- zmiany backendowych uprawnień bez osobnej decyzji produktowej.
