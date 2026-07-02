# GUEST_LINK_AUDIT_20260702

## Kontekst

- Zakres: link majstra/ekipy `/g/...`, czyli link-only guest worker.
- HEAD podczas audytu: `b82886a docs: record current smoke results`.
- Cel: sprawdzić, czy `/g/...` nie dostał pełnej aplikacji, panelu ludzi, listy zleceń ani akcji zarządczych.
- Bez zmian w kodzie aplikacji.

## Frontend

`/g/<token>` trafia do osobnego entrypointu `GuestEntry` w `frontend/src/App.tsx`.

Obecne zachowanie:

- link jest rozwiązywany przez `GET /api/guest/{token}`,
- użytkownik widzi ekran zaproszenia i musi wejść do jednego zlecenia,
- `ProjectView` dostaje `guestToken`,
- offline queue ma osobny scope z `guestOfflineScope(token)`,
- widok nie przechodzi przez pełny `RoleAwareSidebar`,
- link-only nie dostaje menu `Moje zlecenia`, `Ustawienia`, `Wykonawcy`, `Majstrowie i ekipy` ani `Raporty` jako pełnej aplikacji.

W `ProjectView` dla `guestToken`:

- `canAdd` zależy od `project.guest.permission` (`add` albo `history`),
- raporty PDF są ładowane tylko dla `history` albo `view`,
- `canCloseProject` i `canReopenProject` wymagają zalogowanego użytkownika i `!guestToken`,
- link klienta, edycja zlecenia i zarządzanie są ukrywane przez warunki `!guestToken`.

## Backend

`get_project_access(..., allow_guest=True)` dopuszcza gościa tylko gdy:

- projekt istnieje,
- token pasuje do `GuestInvite.token_hash`,
- token jest przypisany do tego samego `project_id`,
- link nie jest cofnięty (`revoked_at is None`),
- link nie wygasł.

Uprawnienia `ProjectAccess` dla gościa:

- `can_view_history()` jest prawdziwe dla `history` albo `view`,
- `can_add()` jest prawdziwe dla `add` albo `history`,
- `can_manage()` jest zawsze fałszywe,
- `can_edit_details()` jest zawsze fałszywe.

Akcje zarządcze:

- `POST /api/projects/{id}/close` używa `allow_guest=False`,
- `POST /api/projects/{id}/start` używa `allow_guest=False`,
- `POST /api/projects/{id}/reopen` używa `allow_guest=False`,
- tworzenie/edycja etapów używa `allow_guest=False` i `require_edit_details()`,
- lista projektów i lista workerów nie działają na samym `x-guest-token`.

## Testy Pokrywające Ryzyka

Istniejące testy potwierdzają:

- link-only widzi tylko projekt przypisany do linku,
- link-only nie wejdzie w inny projekt tym samym tokenem,
- link-only nie ma dostępu do `/api/projects`,
- link-only nie ma dostępu do `/api/workers`,
- guest nie może edytować szczegółów zlecenia,
- guest nie może tworzyć ani widzieć `worker_links`,
- guest nie może usuwać dokumentacji/postępu,
- guest nie może zamknąć ani otworzyć ponownie zlecenia,
- public client `/c/...` nie dostaje prywatnych pól i nie dostaje akcji panelowych,
- guest z `history` może generować i otwierać PDF,
- guest z `add` nie może generować ani listować PDF.

## Ważne Ryzyko Produktowe

`POST /api/projects/{project_id}/stages/{stage_id}/set-current` używa domyślnego `allow_guest=True` i `access.require_add()`.

To oznacza, że link-only z permission `add` albo `history` może ustawić bieżący etap. Jest to zgodne z aktualnym testem `test_project_stage_current_flow`, który oczekuje sukcesu dla guest tokena z uprawnieniem dodawania.

Ocena:

- nie wygląda to jak luka dostępu do pełnej aplikacji,
- ale jest to decyzja produktowa do potwierdzenia,
- jeśli link-only ma tylko dokumentować postęp bez zmiany etapu, trzeba zrobić osobny mały krok ograniczający `set-current` dla `guestToken`.

## Wnioski

Nie znalazłem śladu, żeby `/g/...` dostał pełną aplikację albo panel zarządzania.

Aktualny model jest spójny z założeniem:

- jeden token,
- jedno zlecenie,
- brak listy zleceń,
- brak ludzi/ekip,
- brak edycji danych,
- brak zamykania/reopen,
- brak panelu firmy.

Do sprawdzenia w następnym małym kroku:

1. manualny/mobile smoke `/g/...` dla linku `add`, `history` i `view`,
2. decyzja, czy `Zmień etap` ma zostać dostępne dla link-only,
3. wizualne dopasowanie `/g/...` do obecnego stylu terenowego, jeśli nadal odstaje.

## Rekomendacja

Nie robić teraz refaktoru ani zmian backendu w `/g/...`.

Najbliższy bezpieczny krok:

- `GUEST-LINK-SMOKE-01`: manual/static smoke link-only bez zmian w kodzie,
- albo mały fix tylko wtedy, gdy produktowo zdecydujemy, że link-only nie może zmieniać etapu.
