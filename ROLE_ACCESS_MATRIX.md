# ROLE_ACCESS_MATRIX

Źródło prawdy dla ról i uprawnień po checkpointcie 4D.

Ten dokument stabilizuje kręgosłup ról, opisuje podstawowe statusy z KROKU 5A, finalne zamykanie i ponowne otwieranie z KROKU 5B oraz etap jako kontekst wpisu/postępu z KROKU 5C. Nadal nie opisuje pełnego workflow etapów, zamykania etapów, raportów PDF, audio, płatności ani portfolio.

## Zasady bazowe

- Dostęp aplikacyjny użytkownika wynika z `User.profile_type`.
- Dostęp do firmy/listy wykonawców wynika z `WorkspaceMember.role`.
- Dostęp do zlecenia wynika z `ProjectMember.role` albo z aktywnego `GuestInvite`.
- Link klienta (`/c/...`) jest publicznym podglądem i nie daje dostępu do panelu aplikacji.
- Link majstra/ekipy (`/g/...`) jest dostępem gościa do jednego zlecenia i nie daje dostępu do panelu firmy.

## Helpery i miejsca decyzyjne

Backend:

- `panmajster/access.py`
- `ProjectAccess.can_view_history()`, `can_add()`, `can_manage()`, `can_edit_details()`
- `project_role()`, `get_project_access()`, `user_projects_query()`
- `can_manage_workspace()`
- Predykaty ról: `is_company_owner()`, `is_investor()`, `is_independent_contractor()`, `is_company_worker()`
- Predykaty operacyjne: `can_manage_people()`, `can_create_project()`, `can_manage_workers()`

Frontend:

- `frontend/src/App.tsx`
- Predykaty ról: `isCompanyOwner()`, `isInvestor()`, `isIndependentContractor()`, `isCompanyWorker()`
- Predykaty operacyjne: `canManagePeople()`, `canCreateProject()`, `canSeeTeamPanel()`

Future hardening:

- Jeśli logika ról w UI urośnie, przenieść frontendowe helpery do osobnego pliku, np. `frontend/src/access.ts`.
- Jeśli endpointy workspace/worker będą dalej rosnąć, dodać backendowe helpery `can_assign_worker()`, `can_view_project()` i `can_add_progress()` jako cienkie opakowania nad obecnym `ProjectAccess`.

## 1. Szef firmy / `company_owner`

Widzi:

- panel firmy i zespołu,
- "Majstrowie i ekipy",
- "Zarządzaj ekipami",
- "Zarządzaj pojedynczymi majstrami",
- zlecenia swojej firmy oraz zlecenia, w których jest członkiem projektu.

Nie widzi:

- prywatnych zleceń innych firm,
- panelu inwestora jako własnej roli.

Może tworzyć:

- zlecenia,
- firmę/workspace,
- profile majstrów i ekip,
- majstra bez e-maila jako link-only,
- majstra z e-mailem jako stałe konto po kodzie/zaproszeniu.

Może edytować:

- dane firmy, jeśli ma rolę workspace `owner` albo `admin`,
- profile majstrów i ekip w swojej firmie,
- aktywować/dezaktywować `WorkerProfile`,
- szczegóły zlecenia, jeśli ma rolę projektu `owner`/`manager` albo zgodę wynikającą z `ProjectAccess`.

Może generować linki:

- link dla majstra/ekipy link-only,
- link klienta.

Jakie zlecenia widzi:

- zlecenia, w których istnieje `ProjectMember` dla jego użytkownika,
- przy tworzeniu zlecenia firmowego dostaje rolę projektu `owner`,
- jeśli projekt jest firmowy, szef/właściciel powinien mieć jawny i testowalny dostęp do zlecenia,
- preferowany model to `ProjectMember` z rolą `owner`/`manager`; jeśli dostęp ma wynikać z workspace, równoważna logika musi być jasno opisana i testowana,
- widoczność zleceń firmy nie może opierać się na ukrytej/magicznej logice bez testów regresji.

Backendowe ograniczenia:

- zarządzanie firmą wymaga `can_manage_workspace()`,
- zarządzanie majstrami/ekipami wymaga `can_manage_workers()`,
- dostęp do zlecenia wymaga `get_project_access()`,
- nieaktywny `WorkerProfile` nie może pojawiać się w `/api/workers` do wyboru.

## 2. Inwestor / `investor`

Widzi:

- "Wykonawcy",
- swoje inwestycje/zlecenia,
- listę wykonawców przypisywanych do inwestycji.

Nie widzi:

- "Majstrowie i ekipy" jako własnego zespołu firmy,
- etykiet i flow opisujących go jako "Szef firmy",
- zarządzania składem ekip jak szef firmy,
- języka "majster/ekipa firmy" jako własnego zespołu.

Może tworzyć:

- inwestycje/zlecenia bez wymagania `client_name` i `client_email`,
- własną listę wykonawców,
- wykonawcę do przypisania,
- zewnętrznych wykonawców, np. firmę, ekipę, glazurnika, hydraulika albo tapeciarza.

Może edytować:

- własną listę wykonawców,
- inwestycje, do których ma rolę projektu pozwalającą na edycję.

Może generować linki:

- link dla wykonawcy przypisanego do inwestycji/zlecenia, jeśli dany projekt/flow to obsługuje,
- link klienta/publiczny podgląd tylko wtedy, gdy dany projekt ma taki publiczny widok,
- linki wynikające z uprawnień do projektu, z zastrzeżeniem że UI i teksty powinny pozostać inwestorskie, nie firmowe.

Jakie zlecenia widzi:

- tylko projekty/inwestycje, gdzie ma `ProjectMember`.

Backendowe ograniczenia:

- dostęp do projektów przez `user_projects_query()`,
- lista wykonawców inwestora nie wymaga firmowego workspace typu company,
- tworzenie inwestycji musi akceptować puste dane klienta.

## 3. Samodzielny majster / `independent_contractor`

Widzi:

- własne zlecenia,
- klienta, postęp i raporty dla własnych zleceń zgodnie z rolą projektu.

Nie widzi:

- "Majstrowie i ekipy",
- "Wykonawcy",
- panelu firmy,
- listy dodawania majstrów/ekip.

Może tworzyć:

- własne zlecenia, gdzie sam jest wykonawcą.

Może edytować:

- własne zlecenia zgodnie z `ProjectAccess`.

Może generować linki:

- link klienta,
- nie generuje linku dla majstra/ekipy.

Jakie zlecenia widzi:

- tylko projekty, w których ma `ProjectMember`.

Backendowe ograniczenia:

- `/api/workers` zwraca pustą listę,
- `POST /api/workers` jest zabroniony,
- `POST /api/projects/{id}/guest-links` z `kind=worker` jest zabroniony.

## 4. Majster - członek firmy / `company_worker`

Widzi:

- tylko przypisane zlecenia,
- szczegóły zlecenia potrzebne do pracy,
- historię/postęp zgodnie z rolą projektu.

Nie widzi:

- panelu szefa,
- nieprzypisanych zleceń firmy,
- zarządzania firmą,
- listy majstrów/ekip do wyboru.

Może tworzyć:

- wpis/postęp w przypisanym zleceniu, jeśli ma rolę projektu co najmniej `contributor`.

Może edytować:

- domyślnie aktualizuje postęp, a nie zarządza całym zleceniem,
- nie zarządza firmą ani zleceniem jak szef,
- szczegóły zlecenia tylko wtedy, gdy pozwala na to `ProjectAccess.can_edit_details()`,
- edycja szczegółów zlecenia musi być ograniczona do pól dopuszczonych dla wykonawcy,
- statusy 5A, zamykanie/ponowne otwieranie 5B oraz etap przy wpisie/postępie 5C są opisane; pełne zarządzanie etapami, zamykanie etapów i workflow "Zgłoś gotowe" zostają na później.

Może generować linki:

- nie generuje linku dla majstra/ekipy,
- nie zarządza linkami klienta z panelu firmy.

Jakie zlecenia widzi:

- wyłącznie zlecenia z `ProjectMember` dla swojego użytkownika.

Backendowe ograniczenia:

- `POST /api/projects` jest zabroniony,
- `POST /api/workspaces` jest zabroniony,
- `POST /api/workers` jest zabroniony,
- `/api/workers` zwraca pustą listę,
- dostęp do zleceń idzie przez `user_projects_query()` i `get_project_access()`.

## 5. Majster/ekipa link-only / guest worker link

Widzi:

- tylko jedno zlecenie wskazane przez link `/g/...`,
- dane zlecenia dopuszczone przez uprawnienie linku.

Nie widzi:

- aplikacji po zalogowaniu,
- panelu firmy,
- listy zleceń,
- zespołu/wykonawców.

Może tworzyć:

- wpis/postęp w jednym zleceniu, jeśli `GuestInvite.permission` to `add` albo `history`.

Może edytować:

- nie edytuje firmy, zespołu ani zleceń poza dozwolonym wpisem postępu.

Może generować linki:

- nie generuje żadnych linków.

Jakie zlecenia widzi:

- dokładnie projekt powiązany z aktywnym `GuestInvite`.

Backendowe ograniczenia:

- dostęp wymaga aktywnego, nieodwołanego tokena gościa,
- `ProjectAccess` dla gościa nie ma `user`,
- uprawnienia gościa są ograniczone przez `GuestInvite.permission`.

## 6. Klient link-only / client public link

Widzi:

- publiczny podgląd jednego zlecenia,
- postęp,
- opublikowane raporty dostępne publicznie,
- dane bez prywatnych pól, np. bez `client_email` w publicznym payloadzie.

Nie widzi:

- panelu aplikacji,
- firmy,
- zespołu,
- wykonawców,
- prywatnych linków majstra.

Może tworzyć:

- nic w kroku 4Z.

Może edytować:

- nic w kroku 4Z.

Może generować linki:

- nie generuje linków.

Jakie zlecenia widzi:

- tylko projekt powiązany z aktywnym `client_share_token`.

Backendowe ograniczenia:

- dostęp tylko przez `/api/public/projects/{token}`,
- endpointy aplikacyjne `/api/projects`, `/api/workspaces`, `/api/workers` wymagają sesji użytkownika,
- PIN musi być zweryfikowany, jeśli jest ustawiony.

## KROK 5A - podstawowe statusy zleceń

- Status zlecenia jest widoczny dla każdej roli, która już widzi dane zlecenie: szefa firmy, inwestora, samodzielnego majstra, `company_worker`, majstra link-only i klienta link-only.
- Status `W realizacji` może zostać ustawiony automatycznie po dodaniu postępu przez osobę lub link, które już mają prawo dodać wpis do zlecenia.
- Automatyczna zmiana statusu nie daje żadnej roli nowych uprawnień do firmy, ludzi, linków ani zarządzania zleceniem.
- Pełne zamykanie zlecenia, ponowne otwieranie oraz reguły statusów powiązane z etapami należą do KROKU 5B.

## KROK 5B - finalne zamknięcie i ponowne otwarcie

- Finalnie zamknąć i ponownie otworzyć zlecenie w MVP mogą tylko role właścicielskie z dostępem `owner`/`manager` do projektu: `company_owner`, `investor`, `independent_contractor`.
- `company_worker` i majster/ekipa link-only w tym kroku nie zamykają finalnie zlecenia i nie otwierają go ponownie.
- Później można dodać osobny flow "Zgłoś gotowe" dla wykonawcy albo link-only, ale nie jest to część 5B.
- Klient link-only nigdy nie zamyka zlecenia i nigdy nie otwiera go ponownie.

## KROK 5C - etap przy wpisie/postępie

- Etap w 5C jest kontekstem wpisu/postępu, nie pełnym workflow etapów.
- Role, które już mogą dodać postęp, mogą wskazać etap przy wpisie.
- Klient link-only może widzieć etap przy wpisie, ale go nie zmienia.
- Zamykanie etapów i customowe etapy są poza 5C.

## Parking / przyszłe kroki

KROK 5:

- zamykanie etapów,
- pełne zarządzanie etapami,
- zmiana etapu jako workflow zlecenia,
- osobny flow "Zgłoś gotowe" dla wykonawcy/link-only.

KROK 6:

- PDF/raport generowany przez majstra stałego,
- PDF/raport generowany przez link-only,
- komentarze klienta,
- audio/transkrypcja i limity.

Później:

- pełny skład ekip,
- wykonawcy inwestora z typem/specjalizacją, np. glazurnik, hydraulik, tapeciarz,
- UX: bardziej widoczny "Dodaj majstra / ekipę",
- UX: u szefa label "Wybierz wykonawcę z listy" -> "Wybierz majstra / ekipę z listy".
