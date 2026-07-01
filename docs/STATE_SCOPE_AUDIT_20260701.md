# STATE SCOPE AUDIT — 2026-07-01

## Cel audytu

Celem audytu jest sprawdzenie, czy stan frontendu, storage przeglądarki, cache i pomocnicze mechanizmy offline są izolowane właściwie między użytkownikami, rolami, projektami i linkami publicznymi.

Audyt jest odpowiedzią na błąd, w którym przełączenie trybu `Prosty` / `Rozbudowany` w jednym oknie przeglądarki wpływało na inne konto zalogowane w innym oknie. Ten konkretny błąd został naprawiony commitem `f6be999 fix: scope view mode preference per user`, a niniejszy dokument sprawdza, czy podobne ryzyka istnieją jeszcze w innych miejscach.

## Kontekst

Sprawdzono przede wszystkim frontend:

- `frontend/src/App.tsx`
- `frontend/src/useUiMode.ts`
- `frontend/src/offline.ts`
- `frontend/src/api.ts`
- `frontend/src/RoleAwareSidebar.tsx`
- `frontend/src/IndependentPortfolioPage.tsx`
- `frontend/public/sw.js`

Nie zmieniano kodu aplikacji, backendu, UI ani stylów. Ten dokument jest audytem i checkpointem ryzyk.

## Podsumowanie

| Obszar | Ryzyko | Status | Rekomendacja |
| --- | --- | --- | --- |
| Tryb `Prosty` / `Rozbudowany` | Wcześniej globalny klucz mógł mieszać role i konta | OK po `f6be999` | Zostawić per-user key i dodać test regresji storage key |
| IndexedDB offline queue | Globalna kolejka bez `userId` / `role` / `scope` może próbować synchronizować wpisy po zmianie konta | HIGH | Dodać scope kolejki per user lub per guest token |
| Service worker cache | Shell/static cache może podać stary bundle po deployu lub zmianie roli | MEDIUM | Wersjonować cache i rozważyć szybsze odświeżanie po deployu |
| Globalny stan `App.tsx` | Dużo stanu jest resetowane ręcznie; nowe stany mogą łatwo wypaść z resetu | MEDIUM | Utrzymać `resetSessionView()` jako kontrakt i dodać checklistę dla nowych stanów |
| Portfolio localStorage | Dane MVP są per user, ale lokalne i mogą zostać na wspólnej przeglądarce | LOW | Przy backendowym portfolio przenieść persistence poza localStorage |
| Demo admin token | Token jest trzymany w pamięci komponentu, nie w localStorage | OK / LOW | Nie utrwalać tokenu; czyścić go przy zamknięciu panelu, jeśli flow urośnie |
| Filtry, sortowanie, modale | Obecnie głównie stan w pamięci komponentów | OK / LOW | Jeśli zostaną utrwalone, używać scope user/role/section/project |
| Public `/c/...`, `/g/...`, `/r/...` | Tokeny są route-based albo przekazywane w pamięci, nie w localStorage | OK | Nie utrwalać publicznych tokenów w storage |

## Inventory storage/state

| Nazwa/klucz/stan | Plik | Obecny scope | Właściwy scope | Ryzyko | Notatka |
| --- | --- | --- | --- | --- | --- |
| `panmajster:viewMode:${user.id}` | `frontend/src/useUiMode.ts` | Per user | Per user | LOW | Naprawione. Storage listener reaguje tylko na aktualny key. |
| `panmajster:viewMode:${profile_type}:${email}` fallback | `frontend/src/useUiMode.ts` | Per role + email | Per user, najlepiej `user.id` | LOW | Fallback jest sensowny, ale email w kluczu localStorage to mały ślad PII. |
| `pan-majster-offline` IndexedDB | `frontend/src/offline.ts` | Global browser DB | Per user / per guest token | HIGH | Kolejka zawiera `projectId`, `guestToken`, payload i pliki, ale nie ma `userId` ani właściciela kolejki. |
| `QueuedEntry.projectId` | `frontend/src/offline.ts`, `frontend/src/App.tsx` | Per project | Per project + user/token | HIGH | Przy zmianie konta `syncQueue()` iteruje po wszystkich wpisach w DB. |
| `QueuedEntry.guestToken` | `frontend/src/offline.ts` | Per guest link, opcjonalnie | Per guest token albo authenticated user | MEDIUM | Dobre dla `/g/...`, ale wpisy auth nie mają analogicznego `userId`. |
| `queuedEntries()` / `syncQueue()` | `frontend/src/App.tsx` | Global queue sync | Scope current user/token | HIGH | Synchronizacja uruchamia się po `online` i próbuje wysłać całą kolejkę. Backend powinien blokować obce projekty, ale UX i bezpieczeństwo powinny zaczynać się wcześniej. |
| `selectedProject` | `frontend/src/App.tsx` | Session memory | Session memory | LOW | Czyszczone w `resetSessionView()` przy login/logout. |
| `projects` | `frontend/src/App.tsx` | Session memory | Session memory | LOW | Czyszczone w `resetSessionView()`. Po loginie ładowane z `/projects`. |
| `section` | `frontend/src/App.tsx` | Session memory | Session memory per active user | LOW | Czyszczone do `home`; `visibleSectionForUser()` chroni role przed wejściem w nieprawidłową sekcję. |
| `createOpen` | `frontend/src/App.tsx` | Session memory | Session memory | LOW | Czyszczone w `resetSessionView()`. |
| `toast` | `frontend/src/App.tsx` | Session memory | Session memory | LOW | Nie przechowuje danych wrażliwych, ale może zostać na ekranie po zmianie stanu; niskie ryzyko. |
| `queueCount` | `frontend/src/App.tsx` | Global IndexedDB count | Per current scope | MEDIUM | Liczy całą kolejkę IndexedDB, więc może pokazać liczbę wpisów z poprzedniego konta. |
| `statusFilter`, `sortBy`, `filter` list | `frontend/src/App.tsx` | Component memory | Component memory lub per user/section | LOW | Nie są utrwalane, resetują się po remount. |
| `entryModal`, `choiceProject` | `frontend/src/App.tsx` | Component memory | Component memory | LOW | Stan lokalny flow wpisów; nie jest utrwalany. |
| `guestToken` | `frontend/src/App.tsx`, `frontend/src/api.ts` | Route prop / request header | Per link token | LOW | Nie znaleziono utrwalania w storage. |
| `x-guest-token` | `frontend/src/api.ts` | Per request | Per request | LOW | Poprawny scope requestu. |
| `demoAdminToken` | `frontend/src/App.tsx` | Component memory | Session-only memory | LOW | Nie jest zapisywany w localStorage/sessionStorage. |
| `panmajster_independent_portfolio_profile_${identity}` | `frontend/src/IndependentPortfolioPage.tsx` | Per user id/email | Per user | LOW | Dane lokalnego MVP; fallback `email` może zostawić PII w key. |
| `panmajster_independent_portfolio_${identity}` | `frontend/src/IndependentPortfolioPage.tsx` | Per user id/email | Per user | LOW | Realizacje portfolio zapisane lokalnie; nie mieszają się między `user.id`, jeśli id jest stabilne. |
| `panmajster_independent_portfolio_reviews_${identity}` | `frontend/src/IndependentPortfolioPage.tsx` | Per user id/email | Per user | LOW | Opinie MVP lokalnie per user. |
| Service worker `pan-majster-5d` | `frontend/public/sw.js` | Global origin cache | Versioned app shell cache | MEDIUM | Nie cache'uje `/api/`, ale cache name wygląda historycznie i może podać stary shell/static. |
| Public report PIN | `frontend/src/App.tsx` | Component memory | Component memory | LOW | Nie znaleziono utrwalania PIN w storage. |

## Znalezione ryzyka

### RISK-001 — Offline IndexedDB queue nie ma scope użytkownika

**Poziom:** HIGH

`frontend/src/offline.ts` tworzy globalną bazę `pan-majster-offline` i store `entries`. `QueuedEntry` zawiera `projectId`, opcjonalny `guestToken`, payload i pliki, ale nie zawiera `userId`, `profile_type`, `workspaceId` ani innego scope dla zalogowanego użytkownika.

`frontend/src/App.tsx` w `syncQueue()` pobiera wszystkie wpisy przez `queuedEntries()` i próbuje synchronizować je jeden po drugim. Jeśli użytkownik A zapisze offline wpis, wyloguje się, a użytkownik B zaloguje się w tej samej przeglądarce, aplikacja nadal widzi tę samą kolejkę.

Prawdopodobnie backend nie pozwoli zapisać wpisu do obcego projektu, ale frontend nie powinien próbować synchronizować cudzych lub historycznych wpisów. To jest szczególnie ważne przy kontach demo, link-only `/g/...`, telefonach używanych przez kilka osób i testach w wielu oknach.

**Rekomendacja:**

- Dodać do `QueuedEntry` pole `scope`, np. `{ kind: "user", userId }` albo `{ kind: "guest", tokenHash }`.
- `queuedEntries()` powinno przyjmować scope i zwracać tylko wpisy aktywnego użytkownika/linku.
- `queueCount` powinien liczyć tylko wpisy bieżącego scope.
- Przy logout nie usuwać automatycznie cudzej kolejki bez decyzji, ale nie pokazywać jej i nie synchronizować pod innym kontem.

### RISK-002 — `queueCount` pokazuje globalną kolejkę offline

**Poziom:** MEDIUM

`refreshQueue()` liczy `queuedEntries().length`, czyli całą bazę IndexedDB. W obecnym stanie liczba "czeka" może dotyczyć poprzedniego użytkownika lub link-only.

**Rekomendacja:**

Powiązać licznik z tym samym scope, co synchronizacja offline. Po tej zmianie `queueCount` powinien oznaczać "wpisy czekające dla aktualnego użytkownika/linku".

### RISK-003 — Service worker cache może trzymać stary shell aplikacji

**Poziom:** MEDIUM

`frontend/public/sw.js` używa cache name `pan-majster-5d` i cache'uje shell oraz GET-y poza `/api/`. API nie jest cache'owane, więc nie wygląda to na źródło przecieku danych. Ryzyko dotyczy raczej starego UI po deployu, gdy service worker poda nieaktualny bundle lub `/app`.

**Rekomendacja:**

- Wersjonować cache zgodnie z build/deploy version.
- Rozważyć mechanizm "new version available" lub agresywniejsze czyszczenie shell cache po deployu.
- Utrzymać zasadę: nie cache'ować `/api/`.

### RISK-004 — Globalny stan `App.tsx` wymaga dyscypliny resetowania

**Poziom:** MEDIUM

`frontend/src/App.tsx` trzyma centralnie m.in. `user`, `projects`, `section`, `selectedProject`, `createOpen`, `toast`, `queueCount`, `uiMode`. Obecnie `resetSessionView()` czyści najważniejsze stany przy wejściu w aplikację po loginie i przy logout.

Ryzyko polega na tym, że `App.tsx` jest duży i łatwo dodać nowy stan, który powinien być resetowany przy zmianie użytkownika, ale nie trafi do `resetSessionView()`.

**Rekomendacja:**

- Każdy nowy stan zależny od użytkownika/projektu dopisywać do checklisty resetu.
- Docelowo rozważyć wydzielenie `useSessionState()` lub podobnego modułu, ale nie jako pilny hotfix.
- Przy zmianie `user.id` można dodać test/invariant, że `selectedProject` i modal states nie zostają z poprzedniej sesji.

### RISK-005 — Portfolio MVP jest lokalne i per-user, ale nadal zostaje w przeglądarce

**Poziom:** LOW

`IndependentPortfolioPage.tsx` zapisuje profil, realizacje i opinie w localStorage pod kluczami per `user.id` lub fallback `email`. To nie miesza danych między kontami z różnym id, ale dane zostają w przeglądarce.

To jest akceptowalne jako MVP/prototyp, jeśli użytkownicy rozumieją, że publiczna wizytówka jest lokalna/tymczasowa. Przy prawdziwej funkcji publicznej dane powinny trafić do backendu.

**Rekomendacja:**

- Przy backendowym portfolio przenieść persistence do API.
- Jeśli fallback email zostaje, rozważyć hash zamiast jawnego e-maila w localStorage key.

### RISK-006 — Przyszłe utrwalanie filtrów/sortów może odtworzyć błąd `viewMode`

**Poziom:** LOW

Filtry i sortowanie list są dziś głównie stanem komponentu. To jest bezpieczne, bo resetują się wraz z komponentem. Jeśli w przyszłości zostaną zapisane w localStorage, nie mogą dostać globalnych kluczy typu `panmajster:filter`.

**Rekomendacja:**

Przy każdym nowym storage key wymagać prefiksu scope:

- `user:${user.id}`
- `role:${profile_type}`
- `project:${project.id}`
- `token:${tokenHash}`

### RISK-007 — Demo admin token jest poprawnie nietrwały, ale wymaga utrzymania tej zasady

**Poziom:** LOW

`demoAdminToken` jest stanem komponentu w `AuthModal`, wysyłanym w nagłówku `Authorization: Bearer ...`. Nie znaleziono zapisu do localStorage ani sessionStorage.

**Rekomendacja:**

Nie utrwalać tokenu demo admina. Jeśli panel demo zostanie rozbudowany, dodać jawne czyszczenie tokenu przy zamknięciu modala albo przejściu na inny krok logowania.

## Rzeczy sprawdzone i uznane za OK

- `useUiMode()` używa per-user storage key po `f6be999`.
- Listener `storage` w `useUiMode()` reaguje tylko na aktualny `storageKey`, więc dwa konta w różnych oknach nie powinny już przełączać sobie trybu.
- `sessionStorage` nie jest używany w `frontend/src`.
- `BroadcastChannel` nie jest używany w `frontend/src`.
- Publiczne tokeny `/c/...`, `/g/...`, `/r/...` są przekazywane przez route/prop/request, nie przez localStorage.
- `api.ts` dodaje `x-guest-token` per request, a nie globalnie.
- `resetSessionView()` czyści `selectedProject`, `createOpen`, `projects` i `section` przy wejściu po loginie oraz przy logout.
- `visibleSectionForUser()` sprawdza, czy aktywna sekcja istnieje w menu danej roli.
- `RoleAwareSidebar` nie pokazuje `company_worker` panelu ludzi ani portfolio.
- Service worker pomija `/api/`, więc nie powinien zwracać starych odpowiedzi API z cache.
- Demo admin token nie jest utrwalany w storage.

## BLOCKERY

Brak blockerów krytycznych znalezionych w audycie.

Największe realne ryzyko to `RISK-001`: globalna kolejka offline w IndexedDB. To nie blokuje obecnego UI, ale powinno zostać naprawione przed mocniejszym użyciem offline na telefonach, testami wielokontowymi albo oddaniem aplikacji szerszej grupie.

## FOLLOW-UP

1. **OFFLINE-SCOPE-1** — dodać scope do IndexedDB offline queue.
2. **OFFLINE-SCOPE-2** — `queueCount` i `syncQueue()` filtrowane per user/link.
3. **SW-CACHE-1** — wersjonowanie cache service workera i strategia aktualizacji po deployu.
4. **SESSION-RESET-1** — test regresji, że po zmianie konta `selectedProject`, modal i section nie przenoszą się między rolami.
5. **STORAGE-POLICY-1** — mała zasada w dokumentacji: każdy nowy storage key musi mieć jawny scope.
6. **PORTFOLIO-PERSISTENCE-1** — przy prawdziwej wizytówce przenieść localStorage MVP do backendu.

## Rekomendowana kolejność działań

1. Najpierw naprawić `RISK-001` i `RISK-002`, bo dotyczą realnych danych wpisów, plików i synchronizacji offline.
2. Następnie dodać test/regresję dla `useUiMode` per user, żeby nie wrócił globalny storage key.
3. Potem zrobić lekki update service workera, żeby zmniejszyć ryzyko starego UI po deployach.
4. Dopiero później rozważać większe porządki w `App.tsx`, najlepiej jako osobny refactor bez zmian produktowych.

## Status końcowy audytu

Audyt nie zmienił kodu aplikacji. Dodano tylko ten dokument.

Najważniejsza decyzja: obecny problem `Prosty` / `Rozbudowany` wygląda na naprawiony per-user, ale mechanizm offline queue wymaga osobnego kroku, bo ma podobny typ ryzyka scope, tylko w bardziej wrażliwym obszarze.
